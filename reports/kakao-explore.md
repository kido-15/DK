# 카카오골프예약(golf.kakao.com) 구조 조사 — 중단 보고 (2차)

- 조사 일시: 2026-09-23 (UTC)
- 조사 환경: Claude Code 원격 실행 컨테이너 ("골프" 세션, Linux 6.18.44) — 골팡 수집·Nominatim/OSRM 지오코딩에 계속 써 온 환경
- 대상: `https://golf.kakao.com`
- 브랜치: `claude/stoic-heisenberg-cfuysm`
- 이전 조사: `claude/kakao-explore` 브랜치의 `reports/kakao-explore.md` (2026-09-18) — 그 세션은 외부망 전체가 막혀 `example.com` 조차 안 됐음.

## 🔴 결론: 1단계(네트워크 확인)에서 다시 중단 — 이번엔 원인이 다름

이 세션은 지시서에서 예상한 대로 **일반적인 외부망은 열려 있습니다** (골팡, Nominatim,
OSRM 모두 정상 응답). 하지만 **`golf.kakao.com`(과 `kakao.com` 계열 도메인 전반)은
이 세션의 송신(egress) 허용 목록에 없어서 접속할 수 없습니다.** 이전 조사처럼
"환경이 통째로 막혀서 아무것도 못 봄"이 아니라, **이 프로젝트가 이미 쓰는 도메인
(golfpang.com, nominatim.openstreetmap.org, router.project-osrm.org)만 허용되어
있고 카카오 도메인은 허용 목록 밖**이라는 더 구체적인 사실을 확인했습니다.

지시서 1단계의 중단 조건("접속 자체가 안 되면 확인만 하고 멈춘다"에 준함)에 해당하므로
2~10단계(Playwright 설치, 브라우저 탐색, `explore.py` 실행, `sources.kakao.json` 작성,
수집 테스트, 커버리지 확인)는 **수행하지 않았습니다.** 사이트 구조·로그인 장벽 여부에 대해
이 보고서가 말할 수 있는 것은 없습니다(둘러보기 자체를 못 함).

## 1️⃣ 실행한 명령과 출력 전문

### (1) 대상 사이트 — 4회 연속 재시도, 모두 실패

```
$ curl -sI --max-time 20 https://golf.kakao.com
HTTP/1.1 502 Bad Gateway
Content-Type: text/plain; charset=utf-8
X-Content-Type-Options: nosniff
Content-Length: 20
Connection: close

$ for i in 1 2 3 4; do curl -sS -o /dev/null -w "HTTP=%{http_code}\n" --max-time 20 https://golf.kakao.com; sleep 2; done
curl: (56) CONNECT tunnel failed, response 502   HTTP=000
curl: (56) CONNECT tunnel failed, response 502   HTTP=000
curl: (56) CONNECT tunnel failed, response 502   HTTP=000
curl: (56) CONNECT tunnel failed, response 502   HTTP=000

$ curl -sS --max-time 20 https://golf.kakao.com/robots.txt
curl: (56) CONNECT tunnel failed, response 502   (robots.txt 내용을 받지 못함)
```

### (2) 이 프로젝트가 실제로 쓰는 도메인들 — 정상 동작 (대조군)

```
$ curl -sS -o /dev/null -w "HTTP=%{http_code}\n" --max-time 15 https://www.golfpang.com
HTTP=200   (재시도 1회 후; 첫 시도는 일시적 connection reset — 골팡 쪽 통상적인 변동)

$ curl -sS -o /dev/null -w "HTTP=%{http_code}\n" --max-time 20 https://nominatim.openstreetmap.org
HTTP=302

$ curl -sS -o /dev/null -w "HTTP=%{http_code}\n" --max-time 20 "https://router.project-osrm.org/route/v1/driving/127.0,37.5;127.1,37.6"
HTTP=200
```

→ 이 세션의 외부망은 전체가 막힌 게 아니라 **열려 있고 실제로 씁니다.**

### (3) 카카오 계열·무관 사이트 대조군 — 모두 차단

```
$ curl -sS -o /dev/null -w "HTTP=%{http_code}\n" --max-time 15 https://www.kakao.com
curl: (56) CONNECT tunnel failed, response 403   HTTP=000

$ curl -sS -o /dev/null -w "HTTP=%{http_code}\n" --max-time 15 https://www.naver.com
curl: (56) CONNECT tunnel failed, response 403   HTTP=000

$ curl -sS -o /dev/null -w "HTTP=%{http_code}\n" --max-time 15 https://www.golfzon.com
curl: (56) CONNECT tunnel failed, response 403   HTTP=000

$ curl -sS -o /dev/null -w "HTTP=%{http_code}\n" --max-time 20 https://example.com
curl: (56) CONNECT tunnel failed, response 403   HTTP=000
```

→ `golf.kakao.com` 만 특이하게 403이 아니라 **502**로 거절됩니다(다른 미허용
도메인은 403). 원인은 알 수 없지만(카카오 측 게이트웨이 특성일 수도, 프록시 쪽
분류 차이일 수도), 프록시 상태 엔드포인트는 두 경우 모두 `connect_rejected` /
"policy denial or upstream failure"로 동일하게 기록합니다. 이 프로젝트가 실제로
쓰는 도메인 3개는 전부 정상이고, 무관한 도메인·카카오 계열 도메인은 전부 막힌
일관된 패턴이므로, **허용 목록(allowlist) 정책에 의한 차단으로 판단합니다.**

### (4) 프록시 상태 엔드포인트 근거

```
$ curl -sS "$HTTPS_PROXY/__agentproxy/status"
{
  ...
  "selective": false,
  "noProxy": "... api.anthropic.com, registry.npmjs.org, pypi.org, ... (일반 웹사이트는 없음)",
  "recentRelayFailures": [
    { "kind": "connect_rejected",
      "detail": "gateway answered 502 to CONNECT (policy denial or upstream failure)",
      "host": "golf.kakao.com:443" },
    ... (golf.kakao.com 반복) ...
    { "kind": "ws_closed_mid_exchange",
      "detail": "tunnel closed (code 1006, ...) after 7s",
      "host": "www.golfpang.com:443" }  ← 이건 골팡의 일시적 변동, 재시도로 해결됨
  ]
}
```

`/root/.ccr/README.md` 의 명시적 지침: "403 / 407 from the proxy: The
destination host is not allowed by your organization's egress policy for
this session. Do not retry or route around it — report the blocked host."
502 도 같은 `connect_rejected` 분류로 기록되므로 동일하게 취급해, **더 이상
재시도하거나 우회를 시도하지 않고 여기서 멈췄습니다.**

### (5) DNS

```
$ getent hosts golf.kakao.com
(결과 없음, exit 2)
```

이 세션은 `CLAUDE_CODE_PROXY_RESOLVES_HOSTS=true` 로 프록시가 호스트 이름을
해석하므로 로컬 DNS 실패 자체는 특이사항이 아닙니다(golfpang 등 허용된 도메인도
로컬 DNS로는 안 풀릴 수 있음). 차단의 실제 근거는 (1)(3)(4)의 프록시 CONNECT
거절입니다.

## 2️⃣ Playwright / Chromium — 이번엔 시도하지 않음

접속 자체가 막혀 있어 Playwright 를 설치하고 브라우저로 접근해도 같은 프록시를
지나가므로 결과가 같을 것이 확실합니다. 지시서의 중단 조건(1단계에서 접속 불가)을
따라 `pip3 install playwright` 및 브라우저 탐색은 **실행하지 않았습니다.**
(Chromium 은 이전 보고서와 마찬가지로 `/opt/pw-browsers/` 에 이미 설치되어
있는 것으로 보이나 이번엔 확인하지 않았습니다.)

## 3️⃣ robots.txt

**받지 못했습니다.** 요청이 프록시 단계에서 거절되어 응답 바이트를 하나도 받지
못했습니다.

## 4️⃣ 확인하지 못한 항목

| 요청 항목 | 상태 |
|---|---|
| robots.txt 내용 | 확인하지 못함 (접속 차단) |
| 로그인 없이 티타임 목록까지 도달 가능한지 | 확인하지 못함 |
| 티타임 목록 URL / API | 확인하지 못함 |
| JSON 응답 구조·HTML 셀렉터 | 확인하지 못함 |
| `sources.kakao.json` | 작성하지 않음 (구조를 모르는 채로 추측해 만들지 않음) |

로그인은 시도하지 않았고(자격증명 자체가 없음), 사이트에 아무것도 제출·예약하지
않았습니다. 코드·설정 파일은 아무것도 새로 만들거나 바꾸지 않았습니다.

## 5️⃣ 다음에 할 수 있는 것

이번 조사로 원인이 좁혀졌습니다: **환경(컨테이너) 문제가 아니라 이 세션의 egress
허용 목록 정책 문제**이고, `golf.kakao.com` 이 그 목록에 없습니다(반면
golfpang.com/nominatim/OSRM 은 이미 허용되어 있어 그대로 잘 씁니다).

1. **이 세션(또는 앞으로 만들 세션)의 원격 실행 환경(Environment) 네트워크
   정책에 `golf.kakao.com` 을 허용 목록에 추가**하고 새 세션을 시작하면 그대로
   이어서 조사할 수 있습니다. 페이지가 실제로 어떤 API·정적 자원 도메인을
   부르는지는 접속 후에야 알 수 있으므로, 처음엔 `golf.kakao.com` 만 열고
   시작한 뒤 브라우저 콘솔/`explore.py` 출력에서 추가로 필요한 도메인
   (예: `*.kakao.com`, `*.daumcdn.net` 등)을 확인해 추가하는 방식을 권합니다.
   환경·네트워크 정책 설정 방법: https://code.claude.com/docs/en/claude-code-on-the-web
2. 접속이 허용되면 이 지시서의 1단계부터 그대로 다시 실행하면 됩니다
   (`curl -sI` → `robots.txt` → `pip3 install playwright` [`playwright install`
   은 하지 않음, `/opt/pw-browsers` 그대로 사용] → 로그인 없이 브라우저로
   티타임 목록까지 이동 → `python3 scripts/explore.py "<찾은 URL>" --days 3`).
3. 또는 외부망이 완전히 열린 로컬 환경에서 위 순서를 실행해도 됩니다.

---

이 보고서에는 실제로 실행한 명령과 그 출력만 담았습니다. golf.kakao.com 의
페이지 구조·API·로그인 장벽 여부에 대한 내용은 이번에도 한 줄도 관측되지
않았으므로 적지 않았습니다.

---

# 3차 시도 — 저장소 소유자가 `golf.kakao.com` 을 허용 목록에 추가한 뒤에도 동일하게 차단

- 조사 일시: 2026-09-23 (UTC), 2차 시도 직후 같은 세션 연속 진행
- 지시 내용: "방금 저장소 소유자(kd)가 이 환경의 네트워크 정책에 `golf.kakao.com`
  을 허용 목록에 추가했다. 이제 접속이 될 것으로 예상된다."

## 🔴 결론: 여전히 1단계에서 차단 — 2차 시도와 완전히 동일한 신호

허용 목록에 추가되었다고 들었지만, 실제로 접속을 시도한 결과 **2차 시도와 정확히
같은 방식으로(같은 502, 같은 `connect_rejected` 분류) 계속 차단**되고 있습니다.
반영이 아직 안 됐거나(전파 지연), 추가가 이 세션에는 적용되지 않았거나, 다른
이유가 있을 수 있으나 이 세션에서는 원인을 알 방법이 없습니다.

## 1️⃣ 실행한 명령과 출력 전문

### (1) 최초 확인

```
$ curl -sI --max-time 15 https://golf.kakao.com
HTTP/1.1 502 Bad Gateway
Content-Type: text/plain; charset=utf-8
X-Content-Type-Options: nosniff
Content-Length: 20
Connection: close
```

### (2) 3회 재시도(3초 간격) — 모두 동일하게 실패

```
$ for i in 1 2 3; do curl -sS -o /dev/null -w "HTTP=%{http_code}\n" --max-time 20 https://golf.kakao.com; sleep 3; done
curl: (56) CONNECT tunnel failed, response 502   HTTP=000
curl: (56) CONNECT tunnel failed, response 502   HTTP=000
curl: (56) CONNECT tunnel failed, response 502   HTTP=000
```

### (3) 대조군 재확인 — 이 프로젝트가 쓰는 도메인은 여전히 정상

```
$ curl -sS -o /dev/null -w "golfpang HTTP=%{http_code}\n" --max-time 15 https://www.golfpang.com
(첫 시도: connection reset — golfpang 쪽 통상적인 일시 변동, 2차 보고서와 동일 패턴)
$ curl -sS -o /dev/null -w "golfpang retry HTTP=%{http_code}\n" --max-time 15 https://www.golfpang.com
golfpang retry HTTP=200
```

```
$ curl -sS -o /dev/null -w "kakao.com HTTP=%{http_code}\n" --max-time 15 https://www.kakao.com
curl: (56) CONNECT tunnel failed, response 403   HTTP=000

$ curl -sS -o /dev/null -w "example.com HTTP=%{http_code}\n" --max-time 15 https://example.com
curl: (56) CONNECT tunnel failed, response 403   HTTP=000
```

→ `golf.kakao.com` 만 다시 한번 유독 **502**(다른 미허용 도메인은 403)로,
2차 시도 때와 완전히 동일한 시그니처로 거절됩니다.

### (4) 프록시 상태 엔드포인트 — `recentRelayFailures` 에 계속 `golf.kakao.com` 기록

```
$ curl -sS "$HTTPS_PROXY/__agentproxy/status"
{
  ...
  "noProxy": "... (golf.kakao.com 이나 kakao.com 계열은 여기에 없음, 2차와 동일) ...",
  "recentRelayFailures": [
    { "ts": "2026-09-23T02:21:30.074Z", "kind": "connect_rejected",
      "detail": "gateway answered 502 to CONNECT (policy denial or upstream failure)",
      "host": "golf.kakao.com:443" },
    { "ts": "2026-09-23T02:21:37.128Z", "kind": "connect_rejected", ... "host": "golf.kakao.com:443" },
    { "ts": "2026-09-23T02:21:40.630Z", "kind": "connect_rejected", ... "host": "golf.kakao.com:443" },
    { "ts": "2026-09-23T02:21:44.121Z", "kind": "connect_rejected", ... "host": "golf.kakao.com:443" },
    { "ts": "2026-09-23T02:21:55.284Z", "kind": "ws_closed_mid_exchange",
      "detail": "tunnel closed (code 1006, ...) after 7s", "host": "www.golfpang.com:443" }
    ... (이후 재시도들도 golf.kakao.com 은 전부 동일한 connect_rejected/502)
  ]
}
```

`noProxy` 목록도 2차 시도 때와 글자 그대로 동일합니다 — `golf.kakao.com` 이나
`*.kakao.com` 계열이 추가된 흔적이 이 세션에서는 보이지 않습니다.

## 2️⃣ 이번에도 2단계 이후는 수행하지 않음

지시서의 중단 조건("여전히 막혀 있으면... 그 사실만 정확히 적어서 보고하고
멈춰라")에 그대로 해당하므로, robots.txt 확인·Playwright 설치·브라우저 탐색·
`sources.kakao.json` 작성 등 이후 단계는 **이번에도 수행하지 않았습니다.**
사이트에 로그인 없이 접근해 무엇이 보이는지는 이번 3차 시도로도 전혀 확인하지
못했습니다.

## 3️⃣ 다음에 필요한 것

- 이 세션이 보는 프록시 상태(`/__agentproxy/status`)에는 `golf.kakao.com` 허용이
  반영되어 있지 않습니다. 허용 목록 변경이 **이미 실행 중인 세션에 즉시 반영되지
  않고, 새 세션을 시작해야 적용되는 방식**일 가능성이 있어 보입니다 — 이 점을
  확인해 주시고, 필요하면 새 세션에서 4차 시도를 이어가면 됩니다.
- 혹은 추가한 도메인 표기가 `golf.kakao.com` 과 정확히 일치하지 않았을 가능성
  (와일드카드 필요 여부 등)도 배제할 수 없으나, 이는 이 세션에서 확인할 수 없는
  네트워크 정책 설정 쪽의 문제입니다.
- 새 세션에서도 같은 502가 재현되면, 이번 3차 보고서의 (4) 프록시 상태 출력을
  함께 첨부해 원인 파악에 참고하시면 좋겠습니다.

---

이번 3차 시도에서도 golf.kakao.com 의 페이지 구조·robots.txt·로그인 장벽 여부에
대해 실제로 관측된 내용은 없습니다. 코드·설정 파일은 아무것도 만들거나 바꾸지
않았고, 사이트에 어떤 요청도(둘러보기 이상의) 보내지 않았습니다.
