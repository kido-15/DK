"""드롭다운으로 조건을 골라야 목록이 나오는 가짜 예약 사이트.

엑스골프처럼 시간·날짜를 드롭다운에서 고르고 '검색' 을 눌러야 티타임이
나오는 화면을 흉내 낸 것이다. 주소만 받아 와서는 목록을 볼 수 없으므로,
사람이 만들어 둔 화면을 그대로 읽는 기능을 검증하는 데 쓴다.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# 처음 열면 조건 입력 폼만 있고 목록이 없다.
# '검색' 을 눌러야 자바스크립트가 목록을 그린다.
SEARCH_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>가짜부킹 - 실시간예약</title></head><body>
<h2>실시간 예약</h2>
<form id="f" onsubmit="return false;">
  <select id="region" name="region">
    <option value="">지역 선택</option>
    <option value="gyeonggi" selected>경기</option>
    <option value="gangwon">강원</option>
  </select>
  <select id="tee" name="tee">
    <option value="">시간 선택</option>
    <option value="0600">06:00</option>
    <option value="1100" selected>11:00</option>
    <option value="1500">15:00</option>
  </select>
  <select id="date" name="date">
    <option value="20260920" selected>2026-09-20</option>
    <option value="20260921">2026-09-21</option>
  </select>
  <button id="go" type="button" onclick="search()">검색</button>
</form>

<div id="result"></div>

<script>
var DATA = [
  ["남서울컨트리클럽", "11:12", "168,000원", "2자리"],
  ["레이크사이드CC",   "12:40", "132,000원", "4자리"],
  ["블루원용인",       "13:20", "98,000원",  "3자리"],
  ["골드컨트리클럽",   "14:50", "155,000원", "4자리"]
];
function search() {
  var rows = DATA.map(function (d) {
    return '<tr class="item"><td class="cc">' + d[0] + '</td>' +
           '<td class="tm">' + d[1] + '</td>' +
           '<td class="fee">' + d[2] + '</td>' +
           '<td class="left">' + d[3] + '</td>' +
           '<td><a href="/book">예약</a></td></tr>';
  }).join('');
  document.getElementById('result').innerHTML =
    '<table class="list"><tbody>' + rows + '</tbody></table>';
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            body, ctype = "User-agent: *\nAllow: /\n", "text/plain"
        elif path in ("/", "/booking"):
            body, ctype = SEARCH_PAGE, "text/html"
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


def start(port: int = 0):
    srv = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
