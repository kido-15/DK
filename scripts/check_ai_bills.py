#!/usr/bin/env python3
"""
열린국회정보 Open API에서 "인공지능"이 포함된 국회의원 발의법률안을 조회하고,
새로 발의된 법안이 있으면 이메일로 알림을 보낸다.

필요 환경변수:
  ASSEMBLY_API_KEY   - open.assembly.go.kr에서 발급받은 Open API 인증키
  GMAIL_ADDRESS      - 발신용 Gmail 주소
  GMAIL_APP_PASSWORD - 위 Gmail 계정의 앱 비밀번호(16자리)
  ALERT_TO           - 알림을 받을 이메일 주소 (생략 시 GMAIL_ADDRESS로 발송)
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
from email.mime.text import MIMEText
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_CODE = "nzmimeepazxkubdpn"  # 국회의원 발의법률안
API_BASE = f"https://open.assembly.go.kr/portal/openapi/{API_CODE}"
KEYWORD = "인공지능"
AGE = 22  # 22대 국회
PAGE_SIZE = 100
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "seen_bills.json")

# 기본 User-Agent(urllib 식별자)를 열린국회정보 서버가 차단하는 경우가 있어
# 브라우저와 유사한 User-Agent를 명시적으로 지정한다.
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ai-bill-alert/1.0)",
    "Accept": "application/json",
}

# 열린국회정보 공통 응답 코드 (성공: INFO-000, 결과 없음: DATA-000)
OK_CODES = {"INFO-000"}
EMPTY_CODES = {"DATA-000"}


def fetch_bills(api_key: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        params = {
            "KEY": api_key,
            "Type": "json",
            "pIndex": page,
            "pSize": PAGE_SIZE,
            "AGE": AGE,
            "BILL_NAME": KEYWORD,
        }
        url = f"{API_BASE}?{urlencode(params)}"
        request = Request(url, headers=REQUEST_HEADERS)
        try:
            with urlopen(request, timeout=20) as resp:
                raw = resp.read().decode("utf-8-sig")
        except HTTPError as e:
            body = e.read().decode("utf-8-sig", errors="replace")
            raise RuntimeError(f"HTTP 오류 {e.code} {e.reason}. 응답: {body[:500]}") from e

        data = json.loads(raw)
        block = data.get(API_CODE)
        if not block:
            break

        head_part, body_part = block[0], block[1]
        result = head_part["head"][1]["RESULT"]
        code = result.get("CODE")

        if code in EMPTY_CODES:
            break
        if code not in OK_CODES:
            raise RuntimeError(f"열린국회정보 API 오류 [{code}]: {result.get('MESSAGE')}")

        page_rows = body_part.get("row", [])
        if not page_rows:
            break
        rows.extend(page_rows)

        if len(page_rows) < PAGE_SIZE:
            break
        page += 1

    return rows


def load_seen() -> tuple[set[str], bool]:
    """반환값: (이미 본 의안번호 집합, 첫 실행 여부)"""
    if not os.path.exists(STATE_FILE):
        return set(), True
    with open(STATE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return set(data), len(data) == 0


def save_seen(seen: set[str]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)
        f.write("\n")


def format_bill(bill: dict) -> str:
    name = bill.get("BILL_NAME", "(제목 없음)")
    no = bill.get("BILL_NO", "")
    proposer = bill.get("PROPOSER", "")
    date = bill.get("PROPOSE_DT", "")
    link = bill.get("DETAIL_LINK") or bill.get("LINK_URL") or ""
    return f"- {name} (의안번호 {no})\n  제안자: {proposer} / 제안일: {date}\n  {link}"


def send_email(new_bills: list[dict], gmail_addr: str, gmail_pass: str, to_addr: str) -> None:
    body_lines = [format_bill(b) for b in new_bills]
    body = (
        f'"{KEYWORD}"이(가) 포함된 새 발의법률안이 {len(new_bills)}건 확인되었습니다.\n\n'
        + "\n\n".join(body_lines)
    )

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = f"[국회 알림] 인공지능 관련 법안 {len(new_bills)}건 발의"
    msg["From"] = gmail_addr
    msg["To"] = to_addr

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(gmail_addr, gmail_pass)
        server.sendmail(gmail_addr, [to_addr], msg.as_string())


def main() -> int:
    api_key = os.environ["ASSEMBLY_API_KEY"]
    gmail_addr = os.environ["GMAIL_ADDRESS"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]
    to_addr = os.environ.get("ALERT_TO") or gmail_addr

    bills = fetch_bills(api_key)
    print(f'"{KEYWORD}" 관련 법안 조회 결과: 총 {len(bills)}건')

    seen, is_first_run = load_seen()
    current_ids = {str(b.get("BILL_NO") or b.get("BILL_ID") or "") for b in bills}
    current_ids.discard("")

    new_bills = [
        b for b in bills
        if str(b.get("BILL_NO") or b.get("BILL_ID") or "") not in seen
    ]

    if is_first_run:
        print(f"최초 실행: 기준 데이터 {len(current_ids)}건을 저장하고 이번엔 알림을 보내지 않습니다.")
    elif new_bills:
        print(f"새 법안 {len(new_bills)}건 발견 -> 이메일 전송")
        send_email(new_bills, gmail_addr, gmail_pass, to_addr)
    else:
        print("새로운 법안 없음.")

    save_seen(seen | current_ids)
    return 0


if __name__ == "__main__":
    sys.exit(main())
