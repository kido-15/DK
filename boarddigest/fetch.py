"""게시판 목록 페이지를 가져온다. (표준 라이브러리 urllib만 사용)

국내 기관 사이트 대응 포인트
  - 일부 사이트는 기본 User-Agent를 차단하므로 브라우저 UA를 보낸다
  - 여전히 EUC-KR(CP949)로 내려주는 사이트가 있어 인코딩을 추정한다
  - 간헐적 오류가 흔해 지수 백오프로 재시도한다
"""

from __future__ import annotations

import gzip
import re
import socket
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

_META_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w-]+)""", re.IGNORECASE
)
_XML_ENCODING = re.compile(rb"""<\?xml[^>]+encoding\s*=\s*["']([\w-]+)["']""", re.IGNORECASE)

# 사이트가 잘못 표기하는 경우가 많은 값들을 실제 코덱으로 정규화
_CHARSET_ALIASES = {
    "euc-kr": "cp949",
    "euckr": "cp949",
    "ks_c_5601-1987": "cp949",
    "ksc5601": "cp949",
    "utf8": "utf-8",
}


class FetchError(RuntimeError):
    """수집 실패. 한 사이트가 실패해도 나머지는 계속 처리하기 위해 따로 잡는다."""


def build_url(url: str, params: dict[str, object] | None = None) -> str:
    if not params:
        return url
    query = urlencode({k: str(v) for k, v in params.items()})
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}{query}"


def _decode(raw: bytes, declared: str | None) -> str:
    candidates: list[str] = []

    def add(name: str | None) -> None:
        if not name:
            return
        name = _CHARSET_ALIASES.get(name.strip().lower(), name.strip().lower())
        if name not in candidates:
            candidates.append(name)

    add(declared)
    meta = _META_CHARSET.search(raw[:4096]) or _XML_ENCODING.search(raw[:4096])
    if meta:
        add(meta.group(1).decode("ascii", "ignore"))
    add("utf-8")
    add("cp949")

    for name in candidates:
        try:
            return raw.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _error_summary(body: bytes) -> str:
    """오류 응답 본문에서 HTML 태그를 걷어내고 한 줄로 요약한다."""
    text = _decode(body[:4096], None)
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:100]


def _decompress(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    try:
        if "gzip" in encoding:
            return gzip.decompress(raw)
        if "deflate" in encoding:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, zlib.error):
        return raw  # 헤더는 압축이라는데 실제로는 아닌 경우
    return raw


def fetch_text(
    url: str,
    *,
    params: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    encoding: str | None = None,
    timeout: int = 20,
    retries: int = 3,
    data: dict[str, object] | None = None,
) -> str:
    """URL 본문을 문자열로 가져온다. data를 주면 POST로 보낸다."""
    target = build_url(url, params)
    merged = dict(DEFAULT_HEADERS)
    merged.update(headers or {})

    body = None
    if data:
        body = urlencode({k: str(v) for k, v in data.items()}).encode("utf-8")
        merged.setdefault("Content-Type", "application/x-www-form-urlencoded")

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = Request(target, headers=merged, data=body)
            with urlopen(request, timeout=timeout) as resp:
                raw = _decompress(resp.read(), resp.headers.get("Content-Encoding", ""))
                declared = encoding or resp.headers.get_content_charset()
                return _decode(raw, declared)
        except HTTPError as e:
            detail = _error_summary(e.read())
            last_error = FetchError(
                f"HTTP {e.code} {e.reason}" + (f" - {detail}" if detail else "")
            )
            if e.code in (400, 401, 403, 404, 405):
                break  # 재시도해도 같은 결과
        except (URLError, socket.timeout, OSError) as e:
            last_error = FetchError(f"연결 실패: {e}")
        if attempt < retries - 1:
            time.sleep(2 ** attempt)

    raise FetchError(f"{target} 수집 실패 - {last_error}")
