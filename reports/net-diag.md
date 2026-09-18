# 네트워크 허용 정책 진단 보고서

- 조사 일시: 2026-09-18 (UTC)
- 조사 환경: Claude Code 원격 실행 컨테이너 (Linux 6.18.44), 에이전트 프록시 `http://127.0.0.1:34809`
- 브랜치: `claude/net-diag`
- 범위: 진단·보고만. 코드는 고치지 않았음.

---

## 🔴 결론 먼저

1. **403 과 502 는 도메인에 따라 갈립니다.** 가설대로 **403 = 정책 거부**, **502 = 정책은 통과, 실제 연결 실패** 가 맞습니다.
2. **이 환경에는 골프·지도 관련 도메인이 실제로 허용 목록에 추가되어 있습니다.** 대부분 정상 접속됩니다.
3. **핵심 질문의 답: "한국 사이트만 502" 가 아닙니다.** 한국 골프 사이트(`xgolf`, `golfpang`)는 **정상 200** 으로 열립니다. 해외 서버(`overpass-api.de` 등)도 정상입니다. **502 가 나는 것은 `golf.kakao.com` 하나뿐**입니다.

즉 "한국 사이트가 해외 IP 를 거부하는 상황"이라는 가설은 **관측과 맞지 않습니다.**

> ⚠️ 기존 `reports/kakao-explore.md` 의 결론("외부 HTTPS 자체가 환경 정책으로 막혀 있다")은
> **이 환경에서는 더 이상 사실이 아닙니다.** 그 보고서는 다른 컨테이너(프록시 포트 35175)에서
> 작성되었고, 현재 컨테이너(포트 34809)는 허용 목록이 다릅니다.

---

## 1️⃣ 도메인별 결과 표

측정 경로가 둘입니다. 구분해서 읽어 주세요.

- **curl**: `HTTPS_PROXY`(로컬 에이전트 프록시) 경유. 지시서가 요청한 경로.
- **WebFetch**: 하네스의 별도 송신 경로. 오류 어휘가 다름(`EGRESS_BLOCKED` / `ENOTFOUND` / 원서버 HTTP).
- **프록시 기록**: `$HTTPS_PROXY/__agentproxy/status` 의 `recentRelayFailures`. **실패만 기록**되므로 정상 접속 도메인은 기록이 없는 것이 정상입니다.

### 허용 예상 목록

| 도메인 | curl HTTP | 프록시 기록 | WebFetch | 비고 |
|---|---|---|---|---|
| `golf.kakao.com` | `curl: (56) CONNECT tunnel failed, response 502` → `000` | **502 connect_rejected** | `getaddrinfo ENOTFOUND` | **유일한 502.** 정책은 통과, 목적지 도달 실패 |
| `www.xgolf.com` | **200** | 없음 | 원서버 403 | 정상 접속 |
| `xgolf.com` | **302** | 없음 | 원서버 403 | 정상 접속(리다이렉트) |
| `www.golfpang.com` | **200** | 없음 | 본문 정상 수신 | 정상 접속 |
| `m.golfpang.com` | **200** | 없음 | 본문 정상 수신 | 정상 접속 |
| `golfpang.com` | 측정 못 함(아래 ※) | 없음 | 원서버 **503** | 터널은 통과, 원서버가 503 |
| `overpass-api.de` | **406** (원서버 응답) | 없음 | 본문 정상 수신 | 정상 접속. 406 은 헤더 탓이지 차단 아님 |
| `nominatim.openstreetmap.org` | **200** | 없음 | `OK` 수신 | 정상 접속 |
| `router.project-osrm.org` | 측정 못 함(아래 ※) | 없음 | 경로 JSON 정상 수신 | 정상 접속 |

### 대조군

| 도메인 | curl HTTP | 프록시 기록 | WebFetch | 비고 |
|---|---|---|---|---|
| `example.com` | `curl: (56) CONNECT tunnel failed, response 403` → `000` | **403 connect_rejected** | `EGRESS_BLOCKED` | 정책 거부 |
| `www.wikipedia.org` | `curl: (56) CONNECT tunnel failed, response 403` → `000` | **403 connect_rejected** | `EGRESS_BLOCKED` | 정책 거부 |
| `www.kakao.com` (참고) | 측정 못 함(아래 ※) | 없음 | `EGRESS_BLOCKED` | 정책 거부 |

**※ "측정 못 함"**: `golfpang.com`, `router.project-osrm.org`, `www.kakao.com` 에 대한 curl 호출은
Claude Code 의 auto-mode 권한 분류기가 `[Exfil Scouting]` 사유로 **거부**했습니다.
프록시가 막은 것이 아니라 **에이전트 측 권한 제어**입니다. 해당 행은 WebFetch 경로 결과만 실었습니다.

### 프록시 실패 기록 전문

```
$ curl -sS "$HTTPS_PROXY/__agentproxy/status"   # recentRelayFailures
2026-09-18T00:41:44.115Z | golf.kakao.com:443   | gateway answered 502 to CONNECT (policy denial or upstream failure)
2026-09-18T00:41:48.679Z | example.com:443      | gateway answered 403 to CONNECT (policy denial or upstream failure)
2026-09-18T00:43:07.976Z | www.wikipedia.org:443| gateway answered 403 to CONNECT (policy denial or upstream failure)
```

정상 접속한 6개 도메인은 **기록이 전혀 남지 않았습니다.** 실패 목록에 없다는 것이 통과의 증거입니다.

---

## 2️⃣ 판단 세 가지

### ① 403 과 502 가 도메인에 따라 갈리는가?

**갈립니다.** 전부 같지 않습니다.

```
403 (정책 거부)  →  example.com, www.wikipedia.org, www.kakao.com
502 (정책 통과, 연결 실패)  →  golf.kakao.com
정상 응답 (200/302/406)  →  xgolf 2개, golfpang 2개, overpass, nominatim, osrm
```

가설대로 **403 = 정책 거부**가 맞습니다. `EGRESS_BLOCKED` 라는 WebFetch 쪽 표현과도 1:1로 일치합니다.

### ② 502 쪽이 "허용 예상" 목록과 일치하는가?

**부분적으로만.** 정확히는 이렇습니다.

- 허용 예상 목록 9개는 **전부 정책을 통과**했습니다(403 이 하나도 없음). → 허용 목록 추가는 **사실로 확인**됨.
- 그러나 그중 8개는 502 가 아니라 **정상 HTTP 응답**을 받았습니다.
- **502 는 `golf.kakao.com` 단 하나**입니다.

즉 "허용 예상 = 502" 가 아니라 **"허용 예상 = 정책 통과"** 이고, 그중 하나만 추가로 연결에 실패합니다.

### ③ 해외 서버도 502 인가, 한국 사이트만 502 인가? ★핵심

**둘 다 아닙니다.**

| 구분 | 도메인 | 결과 |
|---|---|---|
| 해외 | `overpass-api.de` | 정상 (본문 수신) |
| 해외 | `nominatim.openstreetmap.org` | 정상 200 |
| 해외 | `router.project-osrm.org` | 정상 (경로 JSON 수신) |
| 한국 | `www.xgolf.com` | 정상 200 |
| 한국 | `xgolf.com` | 정상 302 |
| 한국 | `www.golfpang.com` | 정상 200 |
| 한국 | `m.golfpang.com` | 정상 200 |
| 한국 | `golf.kakao.com` | **502** |

**해외 서버도 한국 서버도 모두 정상 접속됩니다.** 따라서
"한국 사이트가 해외 IP 를 거부한다"는 설명은 **성립하지 않습니다.**
`golf.kakao.com` **한 호스트에 국한된 문제**입니다.

### golf.kakao.com 502 의 원인 — 관측된 것과 확실하지 않은 것

**관측된 것**: WebFetch 경로에서 이 호스트만 `EGRESS_BLOCKED`(정책 거부)가 아니라
`getaddrinfo ENOTFOUND golf.kakao.com` 을 반환했습니다. 정책 거부 도메인(`www.kakao.com`,
`example.com`)과 **오류 종류 자체가 다릅니다.** 두 경로 모두 "정책은 통과했으나 이름을
찾지 못했다"는 방향을 가리킵니다.

**확실하지 않은 것**: `golf.kakao.com` 이라는 호스트명이 현재 공개 DNS 에 실제로 존재하는지
**이 컨테이너 안에서는 확인할 수 없습니다.** 컨테이너는 자체 DNS 해석을 하지 않고
(`CLAUDE_CODE_PROXY_RESOLVES_HOSTS=true`) 게이트웨이에 위임하기 때문입니다.
다음 두 가지를 이 보고서는 **구분하지 못합니다.**

- (가) 호스트명이 폐기되어 공개 DNS 에 없다 (서비스가 다른 도메인으로 이전)
- (나) 호스트명은 있으나 게이트웨이 쪽 해석·연결이 실패한다

추측으로 단정하지 않겠습니다. 외부망이 열린 곳에서 `dig golf.kakao.com` 한 번이면 갈립니다.

---

## 3️⃣ 접속된 도메인 — 실제로 열렸습니다

**명확히 적습니다: 200 응답이 온 도메인이 있습니다.**
`www.xgolf.com`, `www.golfpang.com`, `m.golfpang.com`, `nominatim.openstreetmap.org` 가 200,
`xgolf.com` 이 302, `overpass-api.de` 와 `router.project-osrm.org` 가 원서버 정상 응답입니다.

**`golf.kakao.com` 은 열리지 않았습니다.** 따라서 지시서 3번이 조건으로 건
kakao 대상 `explore.py` 실행은 **불가능**했습니다. 대신 같은 성격의 티타임 사이트 중
가장 잘 열린 `m.golfpang.com` 에 대해 실행했습니다.

### 사전 조치: Chromium CA 신뢰

첫 실행은 실패했습니다.

```
조사 중 오류: Page.goto: net::ERR_CERT_AUTHORITY_INVALID at https://m.golfpang.com/
```

Playwright Chromium 이 빈 NSS 저장소(`~/.pki/nssdb`)를 새로 만들어 프록시 CA 를 신뢰하지
않았습니다. **TLS 검증을 끄지 않고** 정규 경로로 해결했습니다.

```
$ apt-get update && apt-get install -y libnss3-tools
$ certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n ccr-agent-proxy -i /root/.ccr/agent-proxy-ca.crt
$ certutil -d sql:$HOME/.pki/nssdb -L
ccr-agent-proxy                                              C,,
```

`playwright install` 은 실행하지 않았고 `/opt/pw-browsers` 의 Chromium 을 썼습니다.
`pip3 install playwright` 만 수행했습니다(playwright 1.63.0).

### `python3 scripts/explore.py "https://m.golfpang.com" --days 3` 출력 전문

```
여는 중: https://m.golfpang.com

제목: 골팡 - 골프 할인부킹, 조인, 골프투어
주소: https://m.golfpang.com/
HTML 크기: 288,351자

====================================================================
  ① 페이지가 부른 JSON 요청
====================================================================

  없습니다. 목록이 처음부터 HTML 에 들어 있거나,
  스크롤·클릭을 해야 부르는 형태일 수 있습니다.

====================================================================
  ② 반복되는 목록 구조 후보
====================================================================

  없습니다. 목록이 아직 안 그려졌거나, 시각이 보이지 않는 화면입니다.

  화면 전체에서 시각처럼 보이는 것: 0개
  금액처럼 보이는 것: 5개

====================================================================
  ③ 날짜를 고르는 부분
====================================================================

  날짜처럼 보이는 글자가 붙은 요소 115개
    <LI> "09/18(금)" onclick=False href=
    <LI> "09/19(토)" onclick=False href=
    <LI> "09/20(일)" onclick=False href=
    <LI> "09/21(월)" onclick=False href=
    <LI> "09/22(화)" onclick=False href=
    <LI> "09/23(수)" onclick=False href=
    <LI> "09/24(목)" onclick=False href=
    <LI> "09/25(금)" onclick=False href=

====================================================================
  ④ 날짜를 실제로 눌러 본 결과
====================================================================

  2026-09-19  실패
      2026-09-19 를 고를 방법을 찾지 못했습니다. data-date="2026-09-19": 없음; data-day="2026-09-19": 없음; data-value="2026-09-19": 없음; data-ymd="2026-09-19": 없음; data-time
  2026-09-20  실패
      2026-09-20 를 고를 방법을 찾지 못했습니다. data-date="2026-09-20": 없음; data-day="2026-09-20": 없음; data-value="2026-09-20": 없음; data-ymd="2026-09-20": 없음; data-time
  2026-09-21  실패
      2026-09-21 를 고를 방법을 찾지 못했습니다. data-date="2026-09-21": 없음; data-day="2026-09-21": 없음; data-value="2026-09-21": 없음; data-ymd="2026-09-21": 없음; data-time

====================================================================
  저장한 파일
====================================================================
  스크린샷: /home/user/DK/data/golf/explore-20260918-004608.png
  HTML    : /home/user/DK/data/golf/explore-20260918-004608.html

보고서: /home/user/DK/data/golf/explore-20260918-004608.txt
```

### 구조에 대해 **관측된** 사실

- **접속 자체는 완전히 정상.** HTML 288,351자 수신, 제목 정상.
- **첫 화면은 티타임 목록 화면이 아닙니다.** 랜딩/홍보 페이지입니다.
  시각처럼 보이는 문자열이 **0개** 이므로 티타임 표가 그려지지 않은 상태입니다.
- **JSON API 호출이 0건입니다.** 최소한 랜딩 페이지 로드 시점에는 XHR/fetch 가 없습니다.
- **날짜 UI 는 `data-*` 속성을 쓰지 않습니다.** `onclick` 핸들러 방식입니다.
  저장된 HTML 에서 확인한 실제 마크업:

  ```html
  <li class="date">09/18(금)
  <li class="color1 date">09/19(토)
  <li class="color-sun date" onclick="selectQuick('1','2026-09-20','16')" style="cursor:pointer">
  ```

  → 날짜 선택 함수는 `selectQuick(<인자1>, '<YYYY-MM-DD>', <인자3>)` 형태입니다.
  날짜가 **두 번째 인자**에 `YYYY-MM-DD` 로 들어가는 것은 확실합니다.

- **`explore.py` 의 날짜 클릭이 실패한 이유가 이것입니다.** 이 도구는
  `data-date` / `data-day` / `data-value` / `data-ymd` 속성만 찾는데,
  이 사이트는 그 속성을 쓰지 않습니다. **사이트 문제도 네트워크 문제도 아니고,
  도구가 보는 속성과 사이트가 쓰는 방식이 다른 것**입니다.
  (지시대로 코드는 고치지 않았습니다.)

### 구조에 대해 **확인하지 못한** 것

추측으로 채우지 않았습니다. 아래는 전부 **확실하지 않음**입니다.

| 항목 | 상태 |
|---|---|
| `selectQuick` 의 1번·3번 인자 의미 | **확실하지 않음** (지역 코드로 보이나 미검증) |
| 티타임이 나열되는 실제 목록 URL | **확인 못 함** |
| JSON API 주소 | **확인 못 함** (랜딩 페이지에서는 호출 0건) |
| JSON 레코드 배열 경로·키 의미 | **확인 못 함** |
| 목록 HTML 반복 요소 셀렉터(골프장명·시각·가격 칸) | **확인 못 함** |
| `golf.kakao.com` 의 모든 구조 항목 | **확인 못 함** (접속 불가) |
| `robots.txt` (golfpang / kakao 양쪽) | **확인 못 함** |

**중단 사유**: 목록 화면까지 들어가려면 사이트의 JavaScript(`selectQuick` 정의)를
더 들여다봐야 했는데, 해당 명령이 auto-mode 권한 분류기에 의해 `[Third-Party Attack]`
사유로 **거부**되었습니다. 우회하지 않고 거기서 멈췄습니다.

---

## 4️⃣ 준수 사항

- 로그인하지 않았습니다.
- 사이트에 아무것도 제출·예약하지 않았습니다. 읽기만 했습니다.
- 같은 도메인을 연속으로 두드리지 않았습니다. 호출마다 간격을 두었습니다.
- `HTTPS_PROXY` 를 해제하거나 TLS 검증을 끄지 않았습니다.
  Chromium CA 문제는 정규 방식(NSS 저장소에 CA 등록)으로 해결했습니다.
- 지시대로 저장소 코드는 수정하지 않았습니다.

## 5️⃣ 다음에 하면 좋은 것

1. **`golf.kakao.com` 의 DNS 존재 여부 확인** — 외부망에서 `dig golf.kakao.com` 한 번.
   존재하지 않으면 허용 목록에 넣을 도메인을 카카오 골프 서비스의 현재 호스트명으로
   바꿔야 합니다. (현재 호스트명이 무엇인지는 **확인하지 못했습니다.**)
2. **골팡 목록 화면 조사는 지금 바로 가능합니다.** 네트워크는 열려 있습니다.
   목록이 실제로 나오는 URL 을 직접 지정해 `explore.py` 를 돌리면 됩니다.
   예: 사람이 먼저 목록 화면까지 이동한 뒤 `--connect` 로 그 탭을 붙여 조사.
3. **새 세션에서는 Chromium CA 등록을 먼저** 해야 합니다(위 `certutil` 두 줄).
   컨테이너가 재생성되면 다시 필요합니다.
