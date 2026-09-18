# 카카오골프예약(golf.kakao.com) 구조 조사 — 중단 보고

- 조사 일시: 2026-09-18 (UTC)
- 조사 환경: Claude Code 원격 실행 컨테이너 (Linux 6.18.44)
- 대상: `https://golf.kakao.com`
- 브랜치: `claude/kakao-explore`

## 🔴 결론: 1단계(네트워크 확인)에서 중단

이 컨테이너에서는 **golf.kakao.com 에 접속할 수 없습니다.** 외부 HTTPS 자체가
환경의 송신(egress) 정책으로 막혀 있습니다. 지시서 1단계의 중단 조건에 해당하므로
2~5단계(Playwright 설치, 브라우저 탐색, `explore.py` 실행, 구조 분석)는
**수행하지 않았습니다.**

사이트 구조에 대해 이 보고서가 말할 수 있는 것은 없습니다. 아래 "확인하지 못한 항목"에
요청받은 항목을 그대로 두었습니다. 추측으로 채우지 않았습니다.

## 1️⃣ 실제로 실행한 명령과 출력 전문

### (1) 지시서 1단계 명령

```
$ curl -sI https://golf.kakao.com | head -3
HTTP/1.1 502 Bad Gateway
Content-Type: text/plain; charset=utf-8
X-Content-Type-Options: nosniff
```

이 502 는 golf.kakao.com 이 보낸 응답이 아니라, 이 컨테이너와 외부 사이에 있는
**에이전트 프록시가 CONNECT 를 거절한 결과**입니다. (근거는 (3) 참조)

### (2) 대조군 포함 재확인

요청 간격을 2초 이상 두고 확인했습니다.

```
$ curl -sS -o /dev/null -w "%{http_code}\n" --max-time 20 https://example.com
curl: (56) CONNECT tunnel failed, response 403
000

$ curl -sS -o /dev/null -w "%{http_code}\n" --max-time 25 https://golf.kakao.com
curl: (56) CONNECT tunnel failed, response 502
000

$ curl -sS --max-time 25 https://golf.kakao.com/robots.txt
curl: (56) CONNECT tunnel failed, response 502
HTTP=000

$ curl -sS -o /dev/null -w "%{http_code}\n" --max-time 20 https://www.kakao.com
curl: (56) CONNECT tunnel failed, response 403
000
```

**핵심**: `example.com` 같은 무해한 대조군도 403 으로 똑같이 막힙니다.
즉 카카오가 이 요청을 차단한 것이 아니라, **이 실행 환경이 외부망으로 나갈 수 없는
상태**입니다.

### (3) 프록시 상태 — 거절 기록

```
$ curl -sS "$HTTPS_PROXY/__agentproxy/status"
{
  "selective": false,
  "recentRelayFailures": [
    { "ts": "2026-09-18T00:29:20.117Z", "kind": "connect_rejected",
      "detail": "gateway answered 502 to CONNECT (policy denial or upstream failure)",
      "host": "golf.kakao.com:443" },
    { "ts": "2026-09-18T00:29:36.829Z", "kind": "connect_rejected",
      "detail": "gateway answered 403 to CONNECT (policy denial or upstream failure)",
      "host": "example.com:443" },
    { "ts": "2026-09-18T00:29:39.309Z", "kind": "connect_rejected",
      "detail": "gateway answered 502 to CONNECT (policy denial or upstream failure)",
      "host": "golf.kakao.com:443" },
    { "ts": "2026-09-18T00:29:41.777Z", "kind": "connect_rejected",
      "detail": "gateway answered 502 to CONNECT (policy denial or upstream failure)",
      "host": "golf.kakao.com:443" },
    { "ts": "2026-09-18T00:29:44.001Z", "kind": "connect_rejected",
      "detail": "gateway answered 403 to CONNECT (policy denial or upstream failure)",
      "host": "www.kakao.com:443" }
  ]
}
```

허용된 목적지(`noProxy`)는 패키지 저장소와 Anthropic API 뿐입니다:

```
api.anthropic.com, mcp-proxy.anthropic.com, registry.npmjs.org, jsr.io,
npm.jsr.io, pypi.org, files.pythonhosted.org, index.crates.io,
proxy.golang.org, 그리고 localhost·사설망 대역
```

→ `golf.kakao.com` 을 포함해 **일반 웹사이트는 하나도 허용 목록에 없습니다.**

### (4) DNS·직접 연결도 불가

```
$ getent hosts golf.kakao.com
(결과 없음)

$ cat < /dev/null > /dev/tcp/golf.kakao.com/443
bash: line 1: golf.kakao.com: Name or service not known
```

컨테이너 안에서 이름 해석 자체가 되지 않습니다. 프록시를 우회할 경로도 없습니다.

## 2️⃣ Playwright / Chromium 상태 (참고)

- Chromium 은 예고대로 미리 설치되어 있습니다:
  `/opt/pw-browsers/` → `chromium`, `chromium-1194`, `chromium_headless_shell-1194`, `ffmpeg-1011`
- 파이썬 `playwright` 모듈은 **미설치** 상태입니다:
  `ModuleNotFoundError: No module named 'playwright'`
- `pip3 install playwright` 는 pypi.org 가 허용 목록에 있어 성공할 수 있습니다.
  그러나 **설치해도 의미가 없습니다.** 브라우저도 같은 `HTTPS_PROXY`
  (`http://127.0.0.1:35175`)를 지나가고, 위에서 본 대로 그 프록시가 목적지를
  거절합니다. 그래서 설치를 진행하지 않았습니다. 지시서 1단계의 중단 조건을 따랐습니다.
- `playwright install` 은 실행하지 않았습니다.

## 3️⃣ robots.txt

**확인하지 못했습니다.** `https://golf.kakao.com/robots.txt` 요청이 프록시 단계에서
거절되어 내용을 받지 못했습니다. 따라서 어떤 경로가 허용/금지인지 이 보고서는
아무것도 단정하지 않습니다. 실제 수집 설정을 만들기 전에 robots.txt 를 반드시
먼저 읽어야 합니다.

## 4️⃣ 확인하지 못한 항목 (요청받은 전체 목록)

아래는 모두 **확인하지 못함**입니다. 접속이 되지 않아 관측 자료가 0건입니다.

| 요청 항목 | 상태 |
|---|---|
| 티타임 목록이 나오는 실제 전체 URL | 확인하지 못함 |
| 그 화면까지 가는 조작 (날짜 클릭 / 지역 선택 / 검색 버튼) | 확인하지 못함 |
| `explore.py` 출력 전문 | 확인하지 못함 (실행 자체를 못 함) |
| 페이지가 부르는 JSON API 주소 | 확인하지 못함 |
| JSON 응답 구조 (레코드 배열 경로, 각 키의 의미) | 확인하지 못함 |
| 목록 HTML 구조 (반복 요소 셀렉터, 골프장명·시각·가격 칸) | 확인하지 못함 |
| 날짜 선택 UI 의 속성 이름과 값 예시 | 확인하지 못함 |
| HTML 스냅샷 | 없음 (받은 HTML 이 0바이트) |

개인정보 검토: 수집한 응답이 없으므로 `reports/` 에 개인정보가 섞일 여지도 없습니다.
로그인은 시도하지 않았고, 사이트에 아무것도 제출·예약하지 않았습니다.

## 5️⃣ 다음에 하면 되는 것

이 조사는 **환경 설정을 바꾸면 그대로 재실행**할 수 있습니다. 코드 문제가 아닙니다.

1. **송신 정책에 도메인 허용 추가** — 이 세션의 원격 실행 환경(Environment)에서
   네트워크 정책을 `golf.kakao.com` 이 허용되는 설정으로 바꾸고 새 세션을 시작.
   환경·네트워크 정책 설정 방법: https://code.claude.com/docs/en/claude-code-on-the-web
   허용이 필요한 호스트 후보: `golf.kakao.com`, 그리고 페이지가 부르는
   API·정적 자원 도메인(예: `*.kakao.com`, `*.daumcdn.net` — 실제 목록은 접속 후 확인 필요).
2. 접속이 되면 1단계부터 다시:
   `curl -sI https://golf.kakao.com` → `robots.txt` 확인 →
   `pip3 install playwright` (단 `playwright install` 은 금지, `/opt/pw-browsers` 사용) →
   브라우저로 티타임 목록 화면까지 이동 →
   `python3 scripts/explore.py "<찾은주소>" --days 3`
3. 또는 **외부망이 열린 로컬 환경에서** 위 순서를 그대로 실행.
   `scripts/explore.py` 는 이 저장소에 그대로 있고, 인자는 다음과 같습니다
   (코드에서 확인):

   ```
   python3 scripts/explore.py <url> [--connect http://localhost:9222] [--tab N]
                                    [--show] [--browser 이름] [--wait 밀리초]
                                    [--days N] [--no-click] [--out 경로]
   ```

   기본값: `--wait 5000`, `--days 3`. 보고서는 화면에 출력되고
   `data/golf/explore-<시각>.txt` 로도 저장됩니다.
   이미 열어 둔 브라우저 탭을 조사하려면 `--connect` 를 쓰면 로그인 없이
   수동으로 목록 화면까지 이동한 뒤 그 탭을 그대로 분석할 수 있습니다.

---

이 보고서에는 실제로 실행한 명령과 그 출력만 담았습니다. golf.kakao.com 의
페이지 구조·API·셀렉터에 대한 내용은 한 줄도 관측되지 않았으므로 적지 않았습니다.
