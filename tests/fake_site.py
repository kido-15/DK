"""크롤러 검증용 가짜 예약 사이트. 테스트에서만 쓴다."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HTML_PAGE1 = """<html><head><meta charset="utf-8"></head><body>
<table class="tee-list">
 <tr class="row"><td class="name">남서울컨트리클럽</td><td class="time">07:12</td>
   <td class="fee">168,000원</td><td class="rest">2자리</td><td><a href="/book/1">예약</a></td></tr>
 <tr class="row"><td class="name">레이크사이드CC</td><td class="time">오전 8:40</td>
   <td class="fee">21.5만원</td><td class="rest">4자리</td><td><a href="/book/2">예약</a></td></tr>
 <tr class="row"><td class="name">잘못된행</td><td class="time">시간미정</td>
   <td class="fee">-</td><td class="rest"></td><td></td></tr>
</table></body></html>"""

HTML_PAGE2 = """<html><head><meta charset="utf-8"></head><body>
<table class="tee-list">
 <tr class="row"><td class="name">블루원용인</td><td class="time">14:30</td>
   <td class="fee">98,000</td><td class="rest">3자리</td><td><a href="/book/3">예약</a></td></tr>
</table></body></html>"""

HTML_EMPTY = """<html><body><table class="tee-list"></table></body></html>"""

JSON_DATA = {
    "result": {
        "list": [
            {"cc": "제이드팰리스", "tm": "0654", "price": 250000, "left": 4, "id": 77},
            {"cc": "사우스스프링스", "tm": "1120", "price": 132000, "left": 1, "id": 78},
        ]
    }
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/robots.txt"):
            self._send(b"User-agent: *\nDisallow: /private\n", "text/plain")
        elif self.path.startswith("/tee"):
            page = "1"
            if "page=" in self.path:
                page = self.path.split("page=")[1].split("&")[0]
            body = {"1": HTML_PAGE1, "2": HTML_PAGE2}.get(page, HTML_EMPTY)
            self._send(body.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.startswith("/api"):
            self._send(json.dumps(JSON_DATA, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.startswith("/private"):
            self._send(b"<html>secret</html>", "text/html")
        else:
            self.send_response(404)
            self.end_headers()


def start(port: int = 0) -> tuple[HTTPServer, str]:
    srv = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
