"""
AWS Lambda(서울 리전)에서 하루 한 번 실행되는 AI 연구자료 알림 함수.

국내 기관 사이트는 해외 IP를 차단하는 곳이 많아, 서울 리전에서 돌려야 정상 수집된다.
(기존 ai-bill-alert 함수와 같은 이유)

필요 환경변수:
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD, STATE_BUCKET
  ALERT_TO       - 알림 받을 이메일. 여러 명이면 콤마(,)로 구분
  MAX_AGE_DAYS   - (선택) 며칠 이내 자료까지 볼지. 기본값은 sources.json의 값
  REPORT_ERRORS  - (선택) "0"이면 수집 실패 소스를 메일에 싣지 않는다
"""

from __future__ import annotations

import json
import os

import boto3

import collector
import digest

STATE_KEY = "seen_research.json"

s3 = boto3.client("s3")


def load_seen(bucket: str) -> tuple[dict, bool]:
    try:
        obj = s3.get_object(Bucket=bucket, Key=STATE_KEY)
        data = json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        return {}, True
    if isinstance(data, list):        # 예전 형식(키 목록)과의 호환
        data = {k: "1970-01-01" for k in data}
    return data, len(data) == 0


def save_seen(bucket: str, seen: dict) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=STATE_KEY,
        Body=json.dumps(seen, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )


def handler(event, context):
    gmail_addr = os.environ["GMAIL_ADDRESS"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]
    to_addrs = [a.strip() for a in (os.environ.get("ALERT_TO") or gmail_addr).split(",") if a.strip()]
    bucket = os.environ["STATE_BUCKET"]

    config = collector.load_config()
    if os.environ.get("MAX_AGE_DAYS"):
        config["max_age_days"] = int(os.environ["MAX_AGE_DAYS"])

    result = collector.collect(config)
    print(f"수집 완료: {len(result.items)}건, 실패 소스 {len(result.errors)}개")
    for error in result.errors:
        print(f"  [수집실패] {error}")

    seen, is_first_run = load_seen(bucket)
    new_items = digest.filter_new(result.items, seen)
    errors = result.errors if os.environ.get("REPORT_ERRORS", "1") != "0" else []

    if is_first_run:
        message = f"최초 실행: 기준 데이터 {len(result.items)}건 저장, 알림은 보내지 않음"
    elif new_items:
        digest.send_email(new_items, errors, gmail_addr, gmail_pass, to_addrs)
        message = f"신규 자료 {len(new_items)}건 -> 이메일 전송"
    else:
        message = "새로운 자료 없음"

    save_seen(bucket, digest.update_seen(seen, result.items))
    print(message)
    return {
        "statusCode": 200,
        "body": message,
        "collected": len(result.items),
        "new": len(new_items),
        "failed_sources": result.errors,
    }
