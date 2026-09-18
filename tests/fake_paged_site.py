"""페이지를 넘겨야 전량이 나오는 가짜 예약 사이트.

골팡처럼 목록을 POST 로 받아 **HTML 조각**으로 돌려주는 형태를 흉내 냈다.
전량 수집에서 실제로 문제가 되는 것들을 일부러 넣어 두었다.

  /list      : 정상. 마지막 페이지를 넘어가면 빈 표를 준다.
  /clamp     : 마지막 페이지를 넘어가도 **마지막 페이지를 계속 준다**.
               (국내 목록 화면에 흔하다. 그대로 믿으면 같은 매물이 계속 쌓인다)
  /wrongdate : 요청한 날짜와 다른 날짜의 행을 섞어 준다.
  /shift     : 매물이 실시간으로 드나들어 **페이지 경계가 밀리는** 사이트.
               멀리 떨어진 두 페이지가 우연히 같은 내용이 되고, 같은 매물이
               여러 페이지에 걸쳐 들어온다. 골팡이 이렇다.
"""
from __future__ import annotations

import threading
import urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

PER_PAGE = 10
TOTAL = 25                      # 3페이지 (10 + 10 + 5)

HEAD = ('<table class="type2"><thead><tr>'
        '<th>지역</th><th>부킹일</th><th>티타임</th><th>포함</th><th>골프장</th>'
        '<th>홀수</th><th></th><th>그린피</th><th>조회</th>'
        '</tr></thead><tbody>')
FOOT = "</tbody></table>"
EMPTY = ('<table class="type2"><tbody>'
         '<tr><td colspan="9">검색된 티타임이 없습니다.</td></tr>'
         "</tbody></table>")


def _row(i: int, d: date) -> str:
    hour = 6 + (i % 12)
    fee = 90000 + (i % 10) * 10000
    return (f'<tr id="tr_{i:06d}">'
            f"<td>한강이남</td>"
            f"<td>{d:%m월%d일} (토)</td>"
            f"<td>{hour:02d}:{(i * 7) % 60:02d}</td>"
            f'<td><img alt="식사"></td>'
            f"<td>가나{i:03d}컨트리클럽</td>"
            f"<td>18홀</td>"
            f"<td></td>"
            f'<td><span class="price">{fee:,}</span>원</td>'
            f"<td>{i}</td></tr>")


# 페이지가 밀리는 사이트. 2페이지와 4페이지가 우연히 같은 내용이 되도록 짰다.
# 앞서 본 아무 페이지와나 비교해 멈추면, 여기서 4페이지에 멈춰 5페이지를 놓친다.
SHIFT_PAGES = {
    1: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    2: [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    3: [15, 16, 17, 18, 19, 20, 21, 22, 23, 24],   # 앞 페이지와 절반 겹침
    4: [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],   # 2페이지와 우연히 같음
    5: [20, 21, 22, 23, 24],
}


def page_html(page: int, d: date, *, clamp: bool = False,
              wrong_date: bool = False, shift: bool = False) -> str:
    if shift:
        items = SHIFT_PAGES.get(page)
        if not items:
            return EMPTY
        return HEAD + "".join(_row(i, d) for i in items) + FOOT
    last = (TOTAL + PER_PAGE - 1) // PER_PAGE
    if page > last:
        if not clamp:
            return EMPTY
        page = last                      # 범위를 넘어도 마지막 페이지를 준다
    start = (page - 1) * PER_PAGE
    rows = []
    for i in range(start, min(start + PER_PAGE, TOTAL)):
        row_date = d + timedelta(days=1) if (wrong_date and i % 2 == 0) else d
        rows.append(_row(i, row_date))
    return HEAD + "".join(rows) + FOOT


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.split("?")[0] == "/robots.txt":
            return self._send("User-agent: *\nAllow: /\n", "text/plain")
        self.send_response(405)
        self.end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        try:
            page = int(form.get("pageNum", ["1"])[0])
        except ValueError:
            page = 1
        raw = form.get("rd_date", [""])[0]
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            d = date.today()

        if path == "/list":
            body = page_html(page, d)
        elif path == "/clamp":
            body = page_html(page, d, clamp=True)
        elif path == "/wrongdate":
            body = page_html(page, d, wrong_date=True)
        elif path == "/shift":
            body = page_html(page, d, shift=True)
        else:
            self.send_response(404)
            self.end_headers()
            return
        self._send(body, "text/html")

    def _send(self, text: str, ctype: str):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start(port: int = 0):
    srv = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
