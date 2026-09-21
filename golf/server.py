"""웹 대시보드 서버.

외부 웹 프레임워크 없이 표준 라이브러리 http.server만 쓴다.
기본적으로 127.0.0.1에만 바인딩하므로 내 PC에서만 열린다.

    python3 golf_web.py
    → http://127.0.0.1:8899

API
    GET /api/meta                 골프장 수, 소스 목록, 길찾기 제공자 상태
    GET /api/search?origin=...    검색 (아래 param 참고)
    GET /api/regions              지역 목록
"""

from __future__ import annotations

import json
import os
import traceback
import urllib.parse
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .courses import CourseBook
from .geo import Geocoder
from .models import SearchQuery, parse_date, parse_time
from .routing import Router
from .search import GolfSearch
from .sources import CsvSource, SnapshotSource, load_sources

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class AppState:
    """서버가 들고 있는 것들. 요청마다 다시 만들지 않는다."""

    def __init__(
        self,
        book: CourseBook,
        sources: list,
        router: Router,
        geocoder: Geocoder,
    ):
        self.book = book
        self.sources = sources
        self.router = router
        self.geocoder = geocoder
        self.engine = GolfSearch(book, sources, router)


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
        })

    def _api_search(self, params):
        st = self.state
        q, err = build_query(params, st)
        if q is None:
            self._json({"error": err}, 400)
            return

        results, stats = st.engine.search(q)
        self._json({
            "query": {
                "origin": q.origin,
                "origin_lat": q.origin_lat,
                "origin_lon": q.origin_lon,
                "describe": q.describe(),
            },
            "results": [r.to_dict() for r in results],
            "stats": stats.to_dict(),
        })


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
    return AppState(book, sources, router, geocoder)


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
