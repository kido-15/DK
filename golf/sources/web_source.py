"""설정만으로 동작하는 범용 예약 사이트 크롤러.

사이트마다 파이썬 파일을 새로 만들지 않고, config/sources.json 에 요청 방법과
필드 위치만 적어 넣으면 된다. 실제 셀렉터는 scripts/probe_source.py 로
사이트 응답을 먼저 확인한 뒤 채운다.

설정 예시 (config/sources.example.json 참고):

    {
      "id": "example",
      "name": "예시 부킹 사이트",
      "enabled": true,
      "format": "html",
      "request": {
        "url": "https://example.com/tee?date={date:%Y%m%d}&page={page}",
        "method": "GET",
        "headers": {"Referer": "https://example.com/"},
        "delay_seconds": 1.5,
        "pages": {"start": 1, "max": 3, "stop_when_empty": true}
      },
      "list_selector": "table.tee-list tr.row",
      "fields": {
        "course_name": {"selector": "td.name"},
        "tee_time":    {"selector": "td.time"},
        "green_fee":   {"selector": "td.fee"},
        "booking_url": {"selector": "a", "attr": "href"},
        "play_date":   {"from_request": "date"}
      }
    }

크롤링 시 지켜야 할 것:
  - robots.txt를 확인하고 (respect_robots 기본 true) 막힌 경로는 건너뛴다
  - 요청 간격(delay_seconds)을 둔다
  - 로그인이 필요한 페이지는 다루지 않는다
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from datetime import date, datetime
from typing import Any, Optional

from .. import htmlsel
from ..models import TeeTime, parse_date, parse_price, parse_time

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# BeautifulSoup이 있으면 더 견고하므로 우선 사용한다. 없으면 자체 파서로 돌아간다.
try:                                     # pragma: no cover
    from bs4 import BeautifulSoup        # type: ignore
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class HttpClient:
    """쿠키를 유지하고 재시도하는 최소한의 HTTP 클라이언트."""

    def __init__(self, *, headers: Optional[dict] = None, timeout: int = 20, retries: int = 2):
        self.timeout = timeout
        self.retries = retries
        self.headers = {
            "User-Agent": DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            **(headers or {}),
        }
        cookie_jar = urllib.request.HTTPCookieProcessor()
        self.opener = urllib.request.build_opener(cookie_jar)

    def get(self, url: str, *, method: str = "GET", body: Any = None,
            extra_headers: Optional[dict] = None) -> str:
        headers = {**self.headers, **(extra_headers or {})}
        data = None
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            elif isinstance(body, str):
                data = body.encode("utf-8")
                headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            else:
                data = body

        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with self.opener.open(req, timeout=self.timeout) as resp:
                    return _decode(resp.read(), resp.headers)
            except urllib.error.HTTPError as exc:
                # 4xx는 재시도해도 같은 결과이므로 바로 포기한다.
                if 400 <= exc.code < 500 and exc.code not in (408, 429):
                    raise
                last_exc = exc
            except Exception as exc:
                last_exc = exc
            if attempt < self.retries:
                time.sleep(1.5 * (attempt + 1))
        raise last_exc if last_exc else RuntimeError("요청 실패")


def _decode(raw: bytes, headers) -> str:
    encoding = (headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    elif encoding == "deflate":
        try:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:
            pass

    charset = None
    ctype = headers.get("Content-Type") or ""
    m = re.search(r"charset=([\w-]+)", ctype, re.IGNORECASE)
    if m:
        charset = m.group(1)
    if not charset:
        # 국내 사이트는 meta 태그에만 euc-kr을 적어 두는 경우가 흔하다.
        head = raw[:2048].decode("ascii", "ignore")
        m = re.search(r'charset=["\']?([\w-]+)', head, re.IGNORECASE)
        if m:
            charset = m.group(1)
    for enc in [charset, "utf-8", "euc-kr", "cp949"]:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 값 추출
# ---------------------------------------------------------------------------


def json_path(obj: Any, path: str) -> Any:
    """'data.list.0.name' 형태의 경로로 중첩 구조를 따라간다."""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            if not part.lstrip("-").isdigit():
                return None
            try:
                cur = cur[int(part)]
            except IndexError:
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def extract(record: Any, spec: Any, context: dict) -> Optional[str]:
    """필드 설정 하나로 레코드에서 문자열 값을 뽑는다.

    spec이 문자열이면 HTML은 선택자, JSON은 키 경로로 해석한다.
    """
    if spec is None:
        return None
    if isinstance(spec, str):
        spec = {"selector": spec} if not isinstance(record, (dict, list)) else {"path": spec}

    if "const" in spec:
        return str(spec["const"])
    if "from_request" in spec:
        val = context.get(spec["from_request"])
        return None if val is None else str(val)

    value: Optional[str] = None

    if isinstance(record, htmlsel.Node) or _is_bs4_tag(record):
        selector = spec.get("selector")
        node = record
        if selector:
            node = record.select_one(selector)
            if node is None:
                return None
        attr = spec.get("attr")
        if attr:
            value = node.get(attr) if not _is_bs4_tag(node) else node.get(attr, "")
            if isinstance(value, list):
                value = " ".join(value)
        else:
            value = node.text if not _is_bs4_tag(node) else node.get_text(" ", strip=True)
    else:
        raw = json_path(record, spec.get("path", ""))
        if raw is None:
            return None
        value = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False) \
            if isinstance(raw, (dict, list)) else str(raw)

    if value is None:
        return None
    value = value.strip()

    pattern = spec.get("regex")
    if pattern:
        m = re.search(pattern, value, re.DOTALL)
        if not m:
            return None
        value = (m.group(1) if m.groups() else m.group(0)).strip()

    if spec.get("replace"):
        for old, new in spec["replace"].items():
            value = value.replace(old, new)

    prefix = spec.get("prefix", "")
    if prefix and value and not value.startswith(("http://", "https://")):
        value = urllib.parse.urljoin(prefix, value)

    return value


def _is_bs4_tag(obj: Any) -> bool:
    return _HAS_BS4 and obj.__class__.__name__ == "Tag"


# ---------------------------------------------------------------------------
# 소스
# ---------------------------------------------------------------------------


class WebSource:
    """설정 하나로 한 예약 사이트를 크롤링한다."""

    def __init__(self, config: dict):
        self.config = config
        self.id = config.get("id") or "web"
        self.name = config.get("name") or self.id
        self.enabled = bool(config.get("enabled", False))
        self.format = (config.get("format") or "html").lower()
        self.request_cfg = config.get("request") or {}
        self.respect_robots = bool(config.get("respect_robots", True))
        self.client = HttpClient(headers=self.request_cfg.get("headers"))
        self.last_error: str = ""
        self.last_stats: dict[str, Any] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    # -- robots -------------------------------------------------------------

    def _robots_allows(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rp = self._robots.get(origin)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(urllib.parse.urljoin(origin, "/robots.txt"))
            try:
                rp.read()
            except Exception:
                # robots.txt를 못 읽으면 막지 않는다 (없는 사이트가 많다)
                rp = None
            self._robots[origin] = rp
        if rp is None:
            return True
        try:
            return rp.can_fetch(DEFAULT_UA, url)
        except Exception:
            return True

    # -- URL 만들기 ---------------------------------------------------------

    @staticmethod
    def _render(template: str, context: dict) -> str:
        """{date:%Y%m%d}, {page} 같은 자리를 채운다."""
        def repl(m: re.Match) -> str:
            key, _, fmt = m.group(1).partition(":")
            val = context.get(key)
            if val is None:
                return ""
            if fmt and isinstance(val, (date, datetime)):
                return val.strftime(fmt)
            if isinstance(val, (date, datetime)):
                return val.isoformat()
            return str(val)
        return re.sub(r"\{([^}]+)\}", repl, template)

    # -- 수집 ---------------------------------------------------------------

    def fetch(self, dates: list[date]) -> list[TeeTime]:
        """날짜별로 페이지를 돌며 티타임을 모은다. 예외를 밖으로 내보내지 않는다."""
        self.last_error = ""
        results: list[TeeTime] = []
        pages_cfg = self.request_cfg.get("pages") or {}
        page_start = int(pages_cfg.get("start", 1))
        page_max = int(pages_cfg.get("max", 1))
        stop_empty = bool(pages_cfg.get("stop_when_empty", True))
        delay = float(self.request_cfg.get("delay_seconds", 1.0))
        url_tpl = self.request_cfg.get("url") or ""

        if not url_tpl:
            self.last_error = "request.url이 비어 있습니다"
            return []

        requests_made = 0
        errors: list[str] = []

        for d in dates or [date.today()]:
            for page in range(page_start, page_start + page_max):
                context = {"date": d, "page": page}
                url = self._render(url_tpl, context)

                if not self._robots_allows(url):
                    errors.append(f"robots.txt가 막은 주소: {url}")
                    break

                body = self.request_cfg.get("body")
                if isinstance(body, str):
                    body = self._render(body, context)
                elif isinstance(body, dict):
                    body = {k: self._render(str(v), context) for k, v in body.items()}
                    body = urllib.parse.urlencode(body)

                try:
                    text = self.client.get(
                        url,
                        method=(self.request_cfg.get("method") or "GET").upper(),
                        body=body,
                    )
                    requests_made += 1
                except Exception as exc:
                    errors.append(f"{url} → {exc}")
                    break

                try:
                    rows = self._parse(text, context)
                except Exception as exc:
                    errors.append(f"파싱 실패 ({url}): {exc}")
                    break

                results.extend(rows)
                if stop_empty and not rows:
                    break
                if delay > 0:
                    time.sleep(delay)

        self.last_stats = {
            "requests": requests_made,
            "rows": len(results),
            "errors": errors,
        }
        if errors and not results:
            self.last_error = errors[0]
        return results

    def _parse(self, text: str, context: dict) -> list[TeeTime]:
        fields = self.config.get("fields") or {}
        if not fields:
            raise ValueError("fields 설정이 없습니다")

        records: list[Any]
        if self.format == "json":
            data = json.loads(text)
            raw = json_path(data, self.config.get("records_path", ""))
            if raw is None:
                return []
            records = raw if isinstance(raw, list) else [raw]
        else:
            list_selector = self.config.get("list_selector")
            if not list_selector:
                raise ValueError("list_selector 설정이 없습니다")
            if _HAS_BS4:
                soup = BeautifulSoup(text, "html.parser")
                records = soup.select(list_selector)
            else:
                records = htmlsel.parse(text).select(list_selector)

        out: list[TeeTime] = []
        for rec in records:
            tee = self._record_to_teetime(rec, fields, context)
            if tee is not None:
                out.append(tee)
        return out

    def _record_to_teetime(self, rec: Any, fields: dict, context: dict) -> Optional[TeeTime]:
        name = extract(rec, fields.get("course_name"), context)
        if not name:
            return None

        d = parse_date(extract(rec, fields.get("play_date"), context)) or context.get("date")
        t = parse_time(extract(rec, fields.get("tee_time"), context))
        if d is None or t is None:
            return None

        slots_raw = extract(rec, fields.get("slots"), context) or ""
        slots_digits = re.sub(r"[^\d]", "", slots_raw)

        return TeeTime(
            course_name=name,
            play_date=d,
            tee_time=t,
            green_fee=parse_price(extract(rec, fields.get("green_fee"), context)),
            source=self.id,
            booking_url=extract(rec, fields.get("booking_url"), context) or "",
            slots=int(slots_digits) if slots_digits else None,
            hole_info=extract(rec, fields.get("hole_info"), context) or "",
            raw={},
        )


DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "sources.json",
)


def load_sources(path: str = DEFAULT_CONFIG_PATH, *, only_enabled: bool = True) -> list[WebSource]:
    """설정 파일에서 소스 목록을 읽는다. 파일이 없으면 빈 목록."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    configs = data.get("sources") if isinstance(data, dict) else data
    sources = [WebSource(c) for c in (configs or [])]
    if only_enabled:
        sources = [s for s in sources if s.enabled]
    return sources
