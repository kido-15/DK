"""로그인이 필요한 가짜 예약 사이트.

로그인하지 않으면 목록 대신 로그인 화면을 준다.
엑스골프·카카오골프예약처럼 회원만 티타임을 볼 수 있는 사이트를 흉내 낸 것이다.

  /            홈 (로그인 링크)
  /do-login    로그인 처리 — 쿠키를 심는다
  /list        티타임 목록 (쿠키가 있어야 보인다)
  /api/list    같은 목록의 JSON (쿠키가 있어야 보인다)
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

COOKIE_NAME = "fake_session"

HOME = """<html><head><meta charset="utf-8"><title>가짜부킹</title></head><body>
<h1>가짜부킹</h1>
<a href="/do-login" id="login">로그인</a>
<a href="/list">실시간예약</a>
</body></html>"""

LOGIN_WALL = """<html><head><meta charset="utf-8"></head><body>
<div class="msg">회원 로그인 후 이용하실 수 있습니다.</div>
<form action="/do-login"><input type="text" name="id">
<input type="password" name="pw"><button>로그인</button></form>
</body></html>"""

LIST_HTML = """<html><head><meta charset="utf-8"></head><body>
<table class="tee"><tbody>
<tr class="r"><td>남서울컨트리클럽</td><td>06:30</td><td>168,000원</td><td>2자리</td></tr>
<tr class="r"><td>레이크사이드CC</td><td>11:20</td><td>132,000원</td><td>4자리</td></tr>
<tr class="r"><td>블루원용인</td><td>13:40</td><td>98,000원</td><td>3자리</td></tr>
</tbody></table></body></html>"""

API_DATA = {"data": {"list": [
    {"ccName": "남서울컨트리클럽", "teeTime": "0630", "greenFee": 168000},
    {"ccName": "레이크사이드CC", "teeTime": "1120", "greenFee": 132000},
    {"ccName": "블루원용인", "teeTime": "1340", "greenFee": 98000},
]}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _logged_in(self) -> bool:
        return COOKIE_NAME in (self.headers.get("Cookie") or "")

    def _send(self, body: str, ctype: str = "text/html", cookie: str = ""):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            self._send("User-agent: *\nAllow: /\n", "text/plain")
        elif path == "/":
            self._send(HOME)
        elif path == "/do-login":
            # 실제 사이트라면 사용자가 아이디/비밀번호를 넣는 자리.
            # 테스트에서는 방문만 하면 로그인된 것으로 친다.
            self._send(
                '<html><body>로그인되었습니다. <a href="/list">목록</a></body></html>',
                cookie=f"{COOKIE_NAME}=ok; Path=/; Max-Age=86400",
            )
        elif path == "/list":
            self._send(LIST_HTML if self._logged_in() else LOGIN_WALL)
        elif path == "/api/list":
            if self._logged_in():
                self._send(json.dumps(API_DATA, ensure_ascii=False), "application/json")
            else:
                self._send(LOGIN_WALL)
        else:
            self.send_response(404)
            self.end_headers()


def start(port: int = 0):
    srv = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
