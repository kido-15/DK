"""AWS Lambda(서울 리전)에서 매일 실행되는 연구기관 게시판 알림 함수.

scripts/check_boards.py와 같은 로직이며, 상태만 로컬 파일 대신 S3에 저장한다.

필요 환경변수
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD, STATE_BUCKET
  ALERT_TO           알림 받을 이메일. 여러 명이면 콤마(,)로 구분
선택 환경변수
  LOOKBACK_DAYS      조회 기간(일). 기본 1 = 전날 하루
  ALERT_ON_ERROR     수집 실패 시 알림 여부. 기본 1(보냄), 0이면 로그만 남김
  SITES_CONFIG_JSON  sites.json 대신 설정을 환경변수로 직접 넣을 때 사용
  STATE_KEY          S3 상태 파일 키. 기본 seen_board_items.json
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from boarddigest.config import ConfigError, load_config, load_config_text  # noqa: E402
from boarddigest.mail import build_subject, build_text, send_email  # noqa: E402
from boarddigest.runner import commit_state, run_digest  # noqa: E402
from boarddigest.state import S3Store  # noqa: E402


def _load() -> dict:
    inline = os.environ.get("SITES_CONFIG_JSON")
    if inline:
        return load_config_text(inline, source="SITES_CONFIG_JSON")
    return load_config(Path(__file__).resolve().parent / "sites.json")


def handler(event, context):
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    to_addrs = [
        a.strip() for a in (os.environ.get("ALERT_TO") or gmail_address).split(",") if a.strip()
    ]
    bucket = os.environ["STATE_BUCKET"]
    days = max(1, int(os.environ.get("LOOKBACK_DAYS", "1")))
    alert_on_error = os.environ.get("ALERT_ON_ERROR", "1") not in ("0", "false", "False")

    try:
        config = _load()
    except ConfigError as e:
        print(f"설정 오류: {e}")
        return {"statusCode": 500, "body": f"설정 오류: {e}"}

    if not config["sites"]:
        message = "설정된 사이트가 없습니다 (sites.json의 sites 배열이 비어 있음)"
        print(message)
        return {"statusCode": 200, "body": message}

    store = S3Store(bucket, os.environ.get("STATE_KEY", "seen_board_items.json"))
    report = run_digest(config, store, days=days)

    for site in report.sites:
        status = f"{len(site.items)}건" if site.ok else f"실패: {site.error}"
        print(f"[{site.site_name}] 목록 {site.scanned}건 확인 -> 신규 {status}")
        for warning in site.warnings:
            print(f"  경고: {warning}")

    should_send = bool(report.items) or (alert_on_error and report.failed)
    if should_send:
        subject = send_email(
            report,
            gmail_address=gmail_address,
            gmail_app_password=gmail_password,
            to_addrs=to_addrs,
            subject=build_subject(report),
        )
        commit_state(store, report)  # 발송 성공 후에만 '보냄'으로 기록
        message = f"{subject} -> {', '.join(to_addrs)}"
    else:
        message = f"{report.target_label} 신규 자료 없음 - 메일 미발송"

    print(message)
    print(build_text(report))
    return {
        "statusCode": 200,
        "body": message,
        "items": len(report.items),
        "failed": [s.site_id for s in report.failed],
    }
