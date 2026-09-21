"""웹 대시보드 서버.

외부 웹 프레임워크 없이 표준 라이브러리 http.server만 쓴다.
기본적으로 127.0.0.1에만 바인딩하므로 내 PC에서만 열린다.

    python3 golf_web.py
    → http://127.0.0.1:8899

API
    GET  /api/meta                     골프장 수, 소스 목록, 길찾기 제공자 상태
    GET  /api/search?origin=...        검색 (아래 param 참고)
    GET  /api/regions                  지역 목록
    GET  /api/collect/status           지금 모으기 작업이 도는 중인지·어디까지 됐는지
    POST /api/collect/start            검색한 날짜를 그 자리에서 모으기 시작
                                        (본문: {"date": "2026-09-22"})
"""

from __future__ import annotations

import json
import os
import traceback
import urllib.parse
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from . import collect
from .collect_job import CollectJobManager
from .courses import CourseBook
from .geo import Geocoder
from .models import SearchQuery, parse_date, parse_time
from .routing import Router
from .search import GolfSearch
from .sources import CsvSource, SnapshotSource, load_sources

# 검색한 날짜가 스냅샷에 없을 때 화면에서 바로 모을 대상 소스.
# 지금은 실제로 전량 수집이 되는 플랫폼이 골팡뿐이라 이걸로 고정한다.
# 그 설정 자체가 없는 환경(예: 시험 환경)에서는 이 기능이 조용히 꺼진다.
AUTO_COLLECT_SOURCE = "golfpang"

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class AppState:
    """서버가 들고 있는 것들. 요청마다 다시 만들지 않는다."""

    def __init__(
        self,
        book: CourseBook,
        sources: list,
        router: Router,
        geocoder: Geocoder,
        *,
        auto_collect_source: str = "",
    ):
        self.book = book
        self.sources = sources
        self.router = router
        self.geocoder = geocoder
        self.engine = GolfSearch(book, sources, router)
        # 그 소스 설정이 실제로 있을 때만 "지금 모으기" 를 켠다.
        self.auto_collect_source = ""
        if auto_collect_source:
            try:
                collect.load_source_config(auto_collect_source)
                self.auto_collect_source = auto_collect_source
            except collect.ConfigNotFound:
                pass
        self.jobs = CollectJobManager()


def build_query(params: dict[str, list[str]], state: AppState) -> tuple[Optional[SearchQuery], str]:
    """쿼리스트링을 SearchQuery로. 두 번째 값은 오류 메시지."""

    def one(key: str, default: str = "") -> str:
        vals = params.get(key) or []
        return vals[0].strip() if vals else default

    origin = one("origin")
    if not origin:
        return None, "출발 위치를 입력해 주세요."

    coords = state.geocoder.geocode(origin)
    if coords is None:
        return None, (
            f"'{origin}' 의 좌표를 찾지 못했습니다. "
            "건물 번호를 빼고 '강남구 테헤란로' 처럼 줄이거나, '강남역' 같은 "
            "역·건물 이름으로 시도해 보세요. 그래도 안 되면 '37.4979,127.0276' "
            "처럼 좌표를 직접 넣어도 됩니다."
        )

    def as_int(key: str) -> Optional[int]:
        raw = one(key)
        if not raw:
            return None
        digits = "".join(ch for ch in raw if ch.isdigit())
        return int(digits) if digits else None

    regions = [r for r in (one("regions").split(",")) if r.strip()]

    return SearchQuery(
        origin=origin,
        origin_lat=coords[0],
        origin_lon=coords[1],
        play_date=parse_date(one("date")),
        tee_from=parse_time(one("tee_from")),
        tee_to=parse_time(one("tee_to")),
        max_drive_minutes=as_int("max_drive"),
        max_price=as_int("max_price"),
        min_price=as_int("min_price"),
        include_unknown_price=one("include_unknown_price") in ("1", "true", "on", "yes"),
        exclude_nine_holes=one("exclude_nine_holes") in ("1", "true", "on", "yes"),
        regions=[r.strip() for r in regions],
        sort=one("sort", "score") or "score",
        limit=as_int("limit") or 50,
    ), ""


class Handler(BaseHTTPRequestHandler):
    state: AppState = None          # 서버 생성 시 주입

    server_version = "GolfFinder/0.1"

    def log_message(self, fmt, *args):
        # 기본 로그는 시끄러워서 요청 줄만 간단히 남긴다
        print(f"  {self.address_string()} {fmt % args}")

    # -- 응답 도우미 -------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _file(self, name: str, ctype: str):
        path = os.path.join(STATIC_DIR, name)
        if not os.path.exists(path):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    # -- 라우팅 ------------------------------------------------------------

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        params = urllib.parse.parse_qs(parts.query)

        try:
            if path in ("/", "/index.html"):
                self._file("index.html", "text/html; charset=utf-8")
            elif path == "/app.js":
                self._file("app.js", "application/javascript; charset=utf-8")
            elif path == "/style.css":
                self._file("style.css", "text/css; charset=utf-8")
            elif path == "/api/meta":
                self._api_meta()
            elif path == "/api/regions":
                self._json({"regions": self.state.book.regions()})
            elif path == "/api/search":
                self._api_search(params)
            elif path == "/api/collect/status":
                self._api_collect_status()
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception:
            traceback.print_exc()
            self._json({"error": "서버 내부 오류가 발생했습니다. 콘솔 로그를 확인해 주세요."}, 500)

    def do_POST(self):
        parts = urllib.parse.urlsplit(self.path)
        try:
            if parts.path == "/api/collect/start":
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except json.JSONDecodeError:
                    body = {}
                self._api_collect_start(body)
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception:
            traceback.print_exc()
            self._json({"error": "서버 내부 오류가 발생했습니다. 콘솔 로그를 확인해 주세요."}, 500)

    def _api_meta(self):
        st = self.state
        self._json({
            "course_count": len(st.book),
            "regions": st.book.regions(),
            "sources": [
                {"id": s.id, "name": getattr(s, "name", s.id),
                 "enabled": getattr(s, "enabled", True)}
                for s in st.sources
            ],
            "routing": st.router.status(),
            "today": date.today().isoformat(),
            "has_courses": len(st.book) > 0,
            "has_sources": len(st.sources) > 0,
            "auto_collect_source": st.auto_collect_source,
        })

    def _api_search(self, params):
        st = self.state
        q, err = build_query(params, st)
        if q is None:
            self._json({"error": err}, 400)
            return

        results, stats = st.engine.search(q)
        payload = {
            "query": {
                "origin": q.origin,
                "origin_lat": q.origin_lat,
                "origin_lon": q.origin_lon,
                "describe": q.describe(),
            },
            "results": [r.to_dict() for r in results],
            "stats": stats.to_dict(),
        }

        # 그 날짜를 요청했는데 아무것도 못 받아 왔으면, 검색 조건이 아니라
        # "아직 그 날짜를 모아 본 적이 없는 것" 일 수 있다. 이미 시도해서
        # 빈 날짜로 확인된 경우는 다시 권하지 않는다(collect.needs_collect).
        if (st.auto_collect_source and q.play_date is not None
                and stats.fetched == 0
                and collect.needs_collect(st.auto_collect_source, q.play_date)):
            payload["needs_collect"] = {
                "source": st.auto_collect_source,
                "date": q.play_date.isoformat(),
            }

        self._json(payload)

    def _api_collect_status(self):
        st = self.state
        if not st.auto_collect_source:
            self._json({"error": "그 자리에서 모으는 기능이 설정되지 않았습니다."}, 400)
            return
        self._json(st.jobs.status(st.auto_collect_source))

    def _api_collect_start(self, body: dict):
        st = self.state
        if not st.auto_collect_source:
            self._json({"error": "그 자리에서 모으는 기능이 설정되지 않았습니다."}, 400)
            return

        raw_date = str(body.get("date") or "").strip()
        d = parse_date(raw_date)
        if d is None:
            self._json({"error": f"날짜를 이해할 수 없습니다: {raw_date!r}"}, 400)
            return

        result = st.jobs.start(st.auto_collect_source, [d])
        self._json(result)


def make_state(
    courses_path: Optional[str] = None,
    sources_path: Optional[str] = None,
    csv_teetimes: Optional[str] = None,
    routing_providers: Optional[list[str]] = None,
    use_snapshot: bool = False,
    snapshot_path: Optional[str] = None,
    snapshot_only: bool = False,
) -> AppState:
    from .courses import DEFAULT_PATH as COURSES_DEFAULT
    from .sources.web_source import DEFAULT_CONFIG_PATH as SOURCES_DEFAULT

    book = CourseBook.load(courses_path or COURSES_DEFAULT)
    sources = []
    if not snapshot_only:
        sources.extend(load_sources(sources_path or SOURCES_DEFAULT))
    if use_snapshot or snapshot_only:
        sources.append(SnapshotSource(snapshot_path))
    if csv_teetimes:
        sources.append(CsvSource(csv_teetimes, source_id="csv", name="CSV 파일"))

    router = Router(providers=routing_providers)
    geocoder = Geocoder(kakao_key=os.environ.get("KAKAO_REST_API_KEY", ""))
    # 스냅샷을 실제로 쓰는 경우에만 "지금 모으기" 를 켠다. 플랫폼 소스를
    # 직접 쓰는 경우(--sources)는 이미 실시간이라 필요 없다.
    auto_collect = AUTO_COLLECT_SOURCE if (use_snapshot or snapshot_only) else ""
    return AppState(book, sources, router, geocoder,
                    auto_collect_source=auto_collect)


def serve(state: AppState, host: str = "127.0.0.1", port: int = 8899):
    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer((host, port), handler)

    print("=" * 60)
    print(f"  골프장 검색 대시보드  →  http://{host}:{port}")
    print("=" * 60)
    print(f"  골프장 DB : {len(state.book)}곳")
    print(f"  소스      : {len(state.sources)}개 "
          f"({', '.join(getattr(s, 'name', s.id) for s in state.sources) or '없음'})")
    print(f"  길찾기    : {state.router.status()}")
    if len(state.book) == 0:
        print("\n  [경고] 골프장 DB가 비어 있습니다.")
        print("         python3 scripts/fetch_golf_courses.py 를 먼저 실행하세요.")
    if not state.sources:
        print("\n  [경고] 티타임 소스가 없습니다.")
        print("         config/sources.json 을 설정하거나 --teetimes 로 CSV를 넘기세요.")
    print("\n  종료: Ctrl+C\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()
