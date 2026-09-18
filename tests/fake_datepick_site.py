"""날짜를 눌러야 목록이 바뀌는 가짜 예약 사이트.

카카오골프예약처럼 날짜 탭을 클릭하면 그 날짜의 티타임이 나오는 화면을
흉내 냈다. 날짜마다 다른 목록이 나오므로, 날짜를 제대로 눌렀는지 결과로
확인할 수 있다.

  /tabs   : 날짜 탭에 data-date 속성이 있는 형태
  /text   : 속성 없이 "9/20" 같은 글자만 있는 형태
  /static : 날짜를 눌러도 목록이 바뀌지 않는 형태 (잘못 수집하면 안 됨)
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# 날짜별 티타임. 날짜를 제대로 눌렀는지 골프장 이름으로 구분할 수 있다.
DATA = {
    "2026-09-20": [("가나컨트리클럽", "07:10", "168,000원"),
                   ("나다컨트리클럽", "11:20", "132,000원"),
                   ("다라컨트리클럽", "13:40", "98,000원")],
    "2026-09-21": [("마바컨트리클럽", "06:40", "205,000원"),
                   ("바사컨트리클럽", "12:10", "119,000원")],
    "2026-09-22": [("사아컨트리클럽", "08:30", "145,000원")],
}

_ROWS_JS = """
function rowsFor(d) {
  var data = DATA[d] || [];
  if (!data.length) return '<p class="empty">해당 날짜에 예약 가능한 티타임이 없습니다.</p>';
  return '<table class="list"><tbody>' + data.map(function (r) {
    return '<tr class="item"><td class="cc">' + r[0] + '</td>' +
           '<td class="tm">' + r[1] + '</td>' +
           '<td class="fee">' + r[2] + '</td>' +
           '<td class="left">4자리</td></tr>';
  }).join('') + '</tbody></table>';
}
function pick(d) {
  document.getElementById('result').innerHTML = rowsFor(d);
  var tabs = document.querySelectorAll('.daytab');
  for (var i = 0; i < tabs.length; i++) tabs[i].className = 'daytab';
  var el = document.querySelector('[data-date="' + d + '"]');
  if (el) el.className = 'daytab on';
}
"""

TABS_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>가짜부킹 - 실시간예약</title></head><body>
<h2>실시간 예약</h2>
<div class="calendar">
  <a href="#" class="daytab" data-date="2026-09-20" onclick="pick('2026-09-20');return false;">20<br>일</a>
  <a href="#" class="daytab" data-date="2026-09-21" onclick="pick('2026-09-21');return false;">21<br>월</a>
  <a href="#" class="daytab" data-date="2026-09-22" onclick="pick('2026-09-22');return false;">22<br>화</a>
  <a href="#" class="daytab" data-date="2026-09-23" onclick="pick('2026-09-23');return false;">23<br>수</a>
</div>
<div id="result"><p class="empty">날짜를 선택해 주세요.</p></div>
<script>var DATA = __DATA__;
__ROWS__
</script></body></html>"""

TEXT_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>가짜부킹2</title></head><body>
<ul class="calendar">
  <li><a href="#" onclick="pick('2026-09-20');return false;">9/20</a></li>
  <li><a href="#" onclick="pick('2026-09-21');return false;">9/21</a></li>
  <li><a href="#" onclick="pick('2026-09-22');return false;">9/22</a></li>
</ul>
<div id="result"><p class="empty">날짜를 선택해 주세요.</p></div>
<script>var DATA = __DATA__;
__ROWS__
</script></body></html>"""

# 날짜를 눌러도 목록이 그대로인 화면. 여기서 여러 날짜를 모으면
# 같은 목록이 날짜만 바뀌어 중복 저장된다. 그래서 건너뛰어야 한다.
STATIC_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>가짜부킹3</title></head><body>
<div class="calendar">
  <a href="#" data-date="2026-09-20">20</a>
  <a href="#" data-date="2026-09-21">21</a>
</div>
<div id="result"><table class="list"><tbody>
<tr class="item"><td>고정컨트리클럽</td><td>09:00</td><td>150,000원</td></tr>
<tr class="item"><td>불변컨트리클럽</td><td>10:00</td><td>160,000원</td></tr>
<tr class="item"><td>동일컨트리클럽</td><td>11:00</td><td>170,000원</td></tr>
</tbody></table></div></body></html>"""


def _build(tpl: str) -> str:
    import json
    return tpl.replace("__DATA__", json.dumps(DATA, ensure_ascii=False)) \
              .replace("__ROWS__", _ROWS_JS)


ROUTES = {
    "/tabs": _build(TABS_PAGE),
    "/text": _build(TEXT_PAGE),
    "/static": STATIC_PAGE,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            body, ctype = "User-agent: *\nAllow: /\n", "text/plain"
        elif path in ROUTES:
            body, ctype = ROUTES[path], "text/html"
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
