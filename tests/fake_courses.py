"""개별 골프장 홈페이지 크롤러 검증용 가짜 사이트 모음.

실제 골프장 홈페이지에서 흔히 보이는 형태들을 흉내 냈다.
  A: 표 형식 예약 페이지 (가장 흔한 형태)
  B: 카드 형식 + 예약 링크가 이미지
  C: 로그인해야 목록이 보이는 곳
  D: 자바스크립트로 그려지는 화면
  E: 예약 링크가 아예 없는 소개용 홈페이지
  F: 예약 목록을 JSON API로 내려주는 곳
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HOME_A = """<html><head><meta charset="utf-8"><title>가나CC</title>
<script src="https://booking.solution-x.co.kr/widget.js"></script></head><body>
<ul class="gnb">
  <li><a href="/intro">골프장 소개</a></li>
  <li><a href="/course">코스 안내</a></li>
  <li><a href="/rsv/realtime">실시간예약</a></li>
  <li><a href="/rsv/confirm">예약확인</a></li>
  <li><a href="/notice">공지사항</a></li>
</ul></body></html>"""

RSV_A = """<html><head><meta charset="utf-8"></head><body>
<h2>실시간 예약</h2>
<table class="rsv"><tbody>
<tr class="item"><td>06:30</td><td>OUT</td><td>168,000원</td><td>4명</td><td><a href="/rsv/do?t=1">예약</a></td></tr>
<tr class="item"><td>06:48</td><td>IN</td><td>168,000원</td><td>2명</td><td><a href="/rsv/do?t=2">예약</a></td></tr>
<tr class="item"><td>07:06</td><td>OUT</td><td>175,000원</td><td>4명</td><td><a href="/rsv/do?t=3">예약</a></td></tr>
<tr class="item"><td>07:24</td><td>IN</td><td>175,000원</td><td>-</td><td>마감</td></tr>
</tbody></table></body></html>"""

HOME_B = """<html><head><meta charset="utf-8"><title>다라CC</title>
<script src="https://booking.solution-x.co.kr/widget.js"></script></head><body>
<a href="/booking/list"><img src="/img/rsv.png" alt="온라인 예약"></a>
<a href="/guide">이용안내</a></body></html>"""

RSV_B = """<html><head><meta charset="utf-8"></head><body>
<div class="tee"><span class="t">08:12</span><span class="p">142,000원</span>
  <span>예약가능</span><a href="/booking/do/11">선택</a></div>
<div class="tee"><span class="t">08:30</span><span class="p">142,000원</span>
  <span>예약가능</span><a href="/booking/do/12">선택</a></div>
<div class="tee"><span class="t">오후 1:20</span><span class="p">9.8만원</span>
  <span>예약가능</span><a href="/booking/do/13">선택</a></div>
</body></html>"""

HOME_C = """<html><head><meta charset="utf-8"><title>마바CC</title></head><body>
<a href="/member/reserve">실시간예약</a></body></html>"""

RSV_C = """<html><head><meta charset="utf-8"></head><body>
<div class="msg">회원 로그인 후 이용하실 수 있습니다.</div>
<form action="/login" method="post">
  <input type="text" name="uid"><input type="password" name="upw">
  <button>로그인</button></form></body></html>"""

HOME_D = """<html><head><meta charset="utf-8"><title>사아CC</title></head><body>
<a href="/reserve">실시간예약</a></body></html>"""

RSV_D = """<html><head><meta charset="utf-8"></head><body>
<div id="root"></div>
<script src="/static/vendor.js"></script>
<script src="/static/app.js"></script>
<script>window.__INIT__={};</script></body></html>"""

HOME_E = """<html><head><meta charset="utf-8"><title>자차CC</title></head><body>
<ul><li><a href="/intro">골프장 소개</a></li><li><a href="/course">코스</a></li>
<li><a href="/way">오시는 길</a></li></ul></body></html>"""

HOME_F = """<html><head><meta charset="utf-8"><title>카타CC</title></head><body>
<a href="/api/teetime">실시간 예약</a></body></html>"""

API_F = {"result": {"code": "0000", "list": [
    {"teeTime": "0640", "greenFee": 198000, "playDate": "20260920", "state": "Y"},
    {"teeTime": "0658", "greenFee": 198000, "playDate": "20260920", "state": "Y"},
    {"teeTime": "1330", "greenFee": 128000, "playDate": "20260920", "state": "마감"},
]}}

# 골프장마다 독립된 서버를 띄운다. 실제 홈페이지처럼 도메인 루트가 홈이라
# "/rsv/realtime" 같은 절대경로 링크가 그대로 동작한다.
SITES = {
    "a": {"/": (HOME_A, "text/html"), "/rsv/realtime": (RSV_A, "text/html")},
    "b": {"/": (HOME_B, "text/html"), "/booking/list": (RSV_B, "text/html")},
    "c": {"/": (HOME_C, "text/html"), "/member/reserve": (RSV_C, "text/html")},
    "d": {"/": (HOME_D, "text/html"), "/reserve": (RSV_D, "text/html")},
    "e": {"/": (HOME_E, "text/html")},
    "f": {"/": (HOME_F, "text/html"),
          "/api/teetime": (json.dumps(API_F, ensure_ascii=False), "application/json")},
}


def _make_handler(routes: dict):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/robots.txt":
                body, ctype = "User-agent: *\nAllow: /\n", "text/plain"
            elif path in routes:
                body, ctype = routes[path]
            else:
                self.send_response(404)
                self.end_headers()
                return
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return Handler


def start_all() -> tuple[list, dict[str, str]]:
    """가짜 골프장 사이트를 전부 띄운다. (서버 목록, {키: 홈페이지주소})"""
    servers, urls = [], {}
    for key, routes in SITES.items():
        srv = HTTPServer(("127.0.0.1", 0), _make_handler(routes))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        urls[key] = f"http://127.0.0.1:{srv.server_address[1]}/"
    return servers, urls


def stop_all(servers) -> None:
    for srv in servers:
        srv.shutdown()
