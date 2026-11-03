"""
AWS Lambda(서울 리전)에서 실행되는 국회 인공지능 법안 알림 함수.

scripts/check_ai_bills.py와 동일한 로직이지만,
- 상태 저장을 로컬 파일 대신 S3 버킷에 한다 (Lambda는 재실행마다 파일시스템이 초기화됨)
- Gmail 발송 후 별도 git 커밋이 필요 없다 (S3에 바로 기록)

필요 환경변수:
  ASSEMBLY_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD, STATE_BUCKET
  ALERT_TO - 알림 받을 이메일. 여러 명이면 콤마(,)로 구분 (예: a@gmail.com,b@gmail.com)
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import boto3

API_CODE = "nzmimeepazxkubdpn"  # 국회의원 발의법률안
API_BASE = f"https://open.assembly.go.kr/portal/openapi/{API_CODE}"
SUMMARY_API_CODE = "BPMBILLSUMMARY"  # 법률안 제안이유 및 주요내용
SUMMARY_API_BASE = f"https://open.assembly.go.kr/portal/openapi/{SUMMARY_API_CODE}"
KEYWORD = "인공지능"
AGE = 22  # 22대 국회
PAGE_SIZE = 100
STATE_KEY = "seen_bills.json"

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ai-bill-alert/1.0)",
    "Accept": "application/json",
}

OK_CODES = {"INFO-000"}
EMPTY_CODES = {"DATA-000"}

s3 = boto3.client("s3")


def call_api(api_code: str, api_base: str, params: dict) -> list[dict]:
    """열린국회정보 API 한 페이지를 호출해 row 목록을 반환한다. 결과가 없으면 빈 리스트."""
    url = f"{api_base}?{urlencode(params)}"
    request = Request(url, headers=REQUEST_HEADERS)
    try:
        with urlopen(request, timeout=20) as resp:
            raw = resp.read().decode("utf-8-sig")
    except HTTPError as e:
        body = e.read().decode("utf-8-sig", errors="replace")
        raise RuntimeError(f"HTTP 오류 {e.code} {e.reason}. 응답: {body[:500]}") from e

    data = json.loads(raw)
    block = data.get(api_code)
    if not block:
        # 결과가 없을 때는 {"RESULT": {...}} 형태로만 오는 경우가 있다
        result = data.get("RESULT") or {}
        code = result.get("CODE")
        if code and code not in OK_CODES | EMPTY_CODES:
            raise RuntimeError(f"열린국회정보 API 오류 [{code}]: {result.get('MESSAGE')}")
        return []

    head_part, body_part = block[0], block[1]
    result = head_part["head"][1]["RESULT"]
    code = result.get("CODE")

    if code in EMPTY_CODES:
        return []
    if code not in OK_CODES:
        raise RuntimeError(f"열린국회정보 API 오류 [{code}]: {result.get('MESSAGE')}")

    return body_part.get("row", [])


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
        page_rows = call_api(API_CODE, API_BASE, params)
        if not page_rows:
            break
        rows.extend(page_rows)

        if len(page_rows) < PAGE_SIZE:
            break
        page += 1

    return rows


def fetch_summary(api_key: str, bill_no: str) -> str:
    """의안번호로 '제안이유 및 주요내용'을 조회한다. 실패하거나 없으면 빈 문자열."""
    if not bill_no:
        return ""
    params = {"KEY": api_key, "Type": "json", "pIndex": 1, "pSize": 1, "BILL_NO": bill_no}
    try:
        rows = call_api(SUMMARY_API_CODE, SUMMARY_API_BASE, params)
    except Exception as e:  # 요약 조회 실패가 알림 발송 자체를 막지 않도록 한다
        print(f"[경고] 의안번호 {bill_no} 제안이유 조회 실패: {e}")
        return ""
    if not rows:
        return ""
    return (rows[0].get("SUMMARY") or "").strip()


def load_seen(bucket: str) -> tuple[set[str], bool]:
    try:
        obj = s3.get_object(Bucket=bucket, Key=STATE_KEY)
        data = json.loads(obj["Body"].read())
        return set(data), len(data) == 0
    except s3.exceptions.NoSuchKey:
        return set(), True


def save_seen(bucket: str, seen: set[str]) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=STATE_KEY,
        Body=json.dumps(sorted(seen), ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def format_bill(bill: dict) -> str:
    name = bill.get("BILL_NAME", "(제목 없음)")
    no = bill.get("BILL_NO", "")
    proposer = bill.get("PROPOSER", "")
    date = bill.get("PROPOSE_DT", "")
    link = bill.get("DETAIL_LINK") or bill.get("LINK_URL") or ""
    summary = bill.get("_SUMMARY") or "(아직 국회에 등록되지 않았습니다. 위 링크에서 확인해 주세요.)"
    return (
        f"■ {name} (의안번호 {no})\n"
        f"  제안자: {proposer} / 제안일: {date}\n"
        f"  {link}\n\n"
        f"[제안이유 및 주요내용]\n{summary}"
    )


def send_email(new_bills: list[dict], gmail_addr: str, gmail_pass: str, to_addrs: list[str]) -> None:
    body_lines = [format_bill(b) for b in new_bills]
    body = (
        f'"{KEYWORD}"이(가) 포함된 새 발의법률안이 {len(new_bills)}건 확인되었습니다.\n\n'
        + ("\n\n" + "-" * 60 + "\n\n").join(body_lines)
    )

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = f"[국회 알림] 인공지능 관련 법안 {len(new_bills)}건 발의"
    msg["From"] = gmail_addr
    msg["To"] = ", ".join(to_addrs)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(gmail_addr, gmail_pass)
        server.sendmail(gmail_addr, to_addrs, msg.as_string())


def handler(event, context):
    api_key = os.environ["ASSEMBLY_API_KEY"]
    gmail_addr = os.environ["GMAIL_ADDRESS"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]
    to_addrs = [a.strip() for a in (os.environ.get("ALERT_TO") or gmail_addr).split(",") if a.strip()]
    bucket = os.environ["STATE_BUCKET"]

    bills = fetch_bills(api_key)
    print(f'"{KEYWORD}" 관련 법안 조회 결과: 총 {len(bills)}건')

    seen, is_first_run = load_seen(bucket)
    current_ids = {str(b.get("BILL_NO") or b.get("BILL_ID") or "") for b in bills}
    current_ids.discard("")

    new_bills = [
        b for b in bills
        if str(b.get("BILL_NO") or b.get("BILL_ID") or "") not in seen
    ]

    if is_first_run:
        message = f"최초 실행: 기준 데이터 {len(current_ids)}건을 저장하고 알림은 보내지 않음"
    elif new_bills:
        for b in new_bills:
            b["_SUMMARY"] = fetch_summary(api_key, str(b.get("BILL_NO") or ""))
        send_email(new_bills, gmail_addr, gmail_pass, to_addrs)
        message = f"새 법안 {len(new_bills)}건 발견 -> 이메일 전송"
    else:
        message = "새로운 법안 없음"

    save_seen(bucket, seen | current_ids)
    print(message)
    return {"statusCode": 200, "body": message}
