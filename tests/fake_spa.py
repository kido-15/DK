"""브라우저 모드 검증용 가짜 SPA 예약 사이트.

플랫폼 예약 사이트처럼 목록을 자바스크립트로 그린다.
  /spa    : 내부 API를 불러 목록을 그린다 (XHR 캡처 대상)
  /render : API 없이 스크립트가 직접 DOM을 만든다 (화면 읽기 대상)
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

API_DATA = {"code": "0000", "data": {"teeList": [
    {"ccName": "가나컨트리클럽", "teeTime": "0630", "greenFee": 168000,
     "playDate": "20260920", "remain": 4},
    {"ccName": "다라컨트리클럽", "teeTime": "1120", "greenFee": 132000,
     "playDate": "20260920", "remain": 2},
    {"ccName": "마바컨트리클럽", "teeTime": "1340", "greenFee": 98000,
     "playDate": "20260920", "remain": 4},
    {"ccName": "사아컨트리클럽", "teeTime": "1450", "greenFee": 115000,
     "playDate": "20260920", "remain": 3},
]}}

SPA_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>가짜부킹</title></head>
<body><div id="root">불러오는 중...</div>
<script>
fetch('/api/v2/teetimes?playDate=20260920&page=1')
  .then(r => r.json())
  .then(d => {
    const rows = d.data.teeList.map(t =>
      `<tr class="row"><td class="cc">${t.ccName}</td><td class="tm">${t.teeTime.slice(0,2)}:${t.teeTime.slice(2)}</td>
       <td class="fee">${t.greenFee.toLocaleString()}원</td><td>${t.remain}자리</td>
       <td><a href="/book/${t.teeTime}">예약</a></td></tr>`).join('');
    document.getElementById('root').innerHTML =
      `<table class="list"><tbody>${rows}</tbody></table>`;
  });
</script></body></html>"""

RENDER_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>가짜부킹2</title></head>
<body><div id="app"></div>
<script>
const data = [["가나CC","07:10","178,000원","2자리"],["다라CC","12:30","121,000원","4자리"],
              ["마바CC","14:20","99,000원","3자리"]];
document.getElementById('app').innerHTML =
  '<div class="cards">' + data.map(d =>
    `<div class="card"><span class="n">${d[0]}</span><span class="t">${d[1]}</span>
     <span class="p">${d[2]}</span><span class="r">${d[3]}</span></div>`).join('') + '</div>';
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: str, ctype: str):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            self._send("User-agent: *\nAllow: /\n", "text/plain")
        elif path == "/spa":
            self._send(SPA_HTML, "text/html")
        elif path == "/render":
            self._send(RENDER_HTML, "text/html")
        elif path == "/api/v2/teetimes":
            self._send(json.dumps(API_DATA, ensure_ascii=False), "application/json")
        else:
            self.send_response(404)
            self.end_headers()


def start(port: int = 0):
    srv = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
