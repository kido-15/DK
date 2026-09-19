"""페이지를 넘겨야 전량이 나오는 가짜 예약 사이트.

골팡처럼 목록을 POST 로 받아 **HTML 조각**으로 돌려주는 형태를 흉내 냈다.
전량 수집에서 실제로 문제가 되는 것들을 일부러 넣어 두었다.

  /list      : 정상. 마지막 페이지를 넘어가면 빈 표를 준다.
  /clamp     : 마지막 페이지를 넘어가도 **마지막 페이지를 계속 준다**.
               (국내 목록 화면에 흔하다. 그대로 믿으면 같은 매물이 계속 쌓인다)
  /wrongdate : 요청한 날짜와 다른 날짜의 행을 섞어 준다.

표의 모양은 2026-09-19 에 www.golfpang.com/web/round/booking_tblList.do 가
실제로 돌려준 조각을 보고 맞춘 것이다. 실물에서 확인한 것:

  - <tbody> 가 있다. 칸은 12개이고 순서는
    지역·부킹일·티타임·포함·골프장·홀수·(빈칸)·그린피·닉네임·캐디유무·구분·조회.
    그린피 뒤에 칸이 네 개 더 붙어 있어서, 뒤에서부터 세면 어긋난다.
  - 부킹일에 **연도가 없다** ("09월19일 (토)").
  - 그린피는 <span class="price"> 안에 있고 '원' 은 그 밖에 있다.
  - 각 칸에 onclick="showCon('...')" 이 붙어 있다.
  - 목록이 없으면 <td colspan="12">리스트가 없습니다.</td> 한 줄을 준다.
  - 조각이 <div class="table_box_list"> 로 시작하고, 끝의 닫는 태그가
    </div> 가 아니라 </di> 다. 깨진 채로 오는 걸 견뎌야 해서 그대로 뒀다.
  - 맨 위에 "총 <strong>9699</strong> 개 타임이 검색되었습니다.." 가 있다.
"""
from __future__ import annotations

import threading
import urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

PER_PAGE = 10
TOTAL = 25                      # 3페이지 (10 + 10 + 5)

COLUMNS = ["지역", "부킹일", "티타임", "포함", "골프장", "홀수", "",
           "그린피", "닉네임", "캐디유무", "구분", "조회"]


def _wrap(total: int, body: str) -> str:
    """실물처럼 건수 표시와 바깥 div 를 둘러 준다. 닫는 태그 오타까지 그대로."""
    head = "".join(f"<th>{c}</th>" for c in COLUMNS)
    return ('<div class="table_box_list"><div class="stab_box mt40">'
            f'<span class="num_product">총 <strong>{total} </strong>'
            " 개 타임이 검색되었습니다..</span></div>"
            '<table class="type2"><thead><tr>' + head + "</tr></thead><tbody>"
            + body +
            "</tbody></table></di></div>")


EMPTY_CELL = f'<tr><td colspan="{len(COLUMNS)}">리스트가 없습니다.</td></tr>'


def _row(i: int, d: date) -> str:
    hour = 6 + (i % 12)
    fee = 90000 + (i % 10) * 10000
    click = f'onclick="showCon(\'{i:06d}\')" style="cursor:pointer"'
    return (f'<tr id="tr_{i:06d}">'
            f"<td {click}>한강이남</td>"
            f"<td {click}>{d:%m월%d일} (토)</td>"
            f"<td {click}>{hour:02d}:{(i * 7) % 60:02d}</td>"
            f'<td {click}><img alt="식사"></td>'
            f"<td {click} align=\"left\">가나{i:03d}컨트리클럽</td>"
            f"<td {click}>18홀</td>"
            f"<td></td>"
            f'<td {click}><span class="price">{fee:,}</span>원</td>'
            f"<td>골팡 김대리</td>"
            f'<td class="state">운전캐디</td>'
            f"<td>양도</td>"
            f"<td>{i % 4}</td></tr>")


def page_html(page: int, d: date, *, clamp: bool = False,
              wrong_date: bool = False) -> str:
    last = (TOTAL + PER_PAGE - 1) // PER_PAGE
    if page > last:
        if not clamp:
            # 실물 골팡은 마지막 페이지를 넘기면 표는 그대로 두고 행만 비운다.
            # 건수 표시는 그대로 남아 있다.
            return _wrap(TOTAL, EMPTY_CELL)
        page = last                      # 범위를 넘어도 마지막 페이지를 준다
    start = (page - 1) * PER_PAGE
    rows = []
    for i in range(start, min(start + PER_PAGE, TOTAL)):
        row_date = d + timedelta(days=1) if (wrong_date and i % 2 == 0) else d
        rows.append(_row(i, row_date))
    return _wrap(TOTAL, "".join(rows))


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
