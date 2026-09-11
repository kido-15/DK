"""
수집 결과를 '신규만 골라내기 → 메일 본문 만들기 → 보내기' 로 엮는 부분.

상태 저장 방식(S3 / 로컬 파일)에 의존하지 않도록, seen 딕셔너리를 인자로 받고
갱신된 딕셔너리를 돌려주는 형태로 분리했다. Lambda와 로컬 스크립트가 같은 코드를 쓴다.
"""

from __future__ import annotations

import smtplib
import ssl
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from collector import KST, Item

SEEN_RETENTION_DAYS = 200


def filter_new(items: list[Item], seen: dict) -> list[Item]:
    return [item for item in items if item.key() not in seen]


def update_seen(seen: dict, items: list[Item], today: str | None = None) -> dict:
    """본 항목을 기록하고, 오래된 기록은 정리해 상태 파일이 무한정 커지지 않게 한다."""
    stamp = today or datetime.now(KST).strftime("%Y-%m-%d")
    updated = dict(seen)
    for item in items:
        updated.setdefault(item.key(), stamp)

    cutoff = (datetime.strptime(stamp, "%Y-%m-%d") - timedelta(days=SEEN_RETENTION_DAYS)).strftime("%Y-%m-%d")
    return {k: v for k, v in updated.items() if v >= cutoff}


def group_by_category(items: list[Item]) -> dict[str, list[Item]]:
    grouped: dict[str, list[Item]] = {}
    for item in items:
        grouped.setdefault(item.category or "기타", []).append(item)
    return grouped


def build_subject(items: list[Item]) -> str:
    today = datetime.now(KST).strftime("%m/%d")
    return f"[AI 연구자료 알림 {today}] 신규 {len(items)}건"


def build_text(items: list[Item], errors: list[str]) -> str:
    lines = [f"AI 관련 신규 공개자료 {len(items)}건", ""]
    for category, group in group_by_category(items).items():
        lines.append(f"■ {category} ({len(group)}건)")
        for item in group:
            date = item.published or "날짜미상"
            lines.append(f"  - [{item.source_name}] {item.title} ({date})")
            lines.append(f"    {item.url}")
        lines.append("")
    if errors:
        lines.append("── 수집에 실패한 소스 (설정 점검 필요) ──")
        lines += [f"  ! {e}" for e in errors]
    return "\n".join(lines)


def build_html(items: list[Item], errors: list[str]) -> str:
    today = datetime.now(KST).strftime("%Y년 %m월 %d일")
    parts = [
        '<div style="font-family:-apple-system,\'Malgun Gothic\',sans-serif;'
        'max-width:760px;color:#1a1a1a;line-height:1.55">',
        f'<h2 style="margin:0 0 4px">AI 연구자료 알림</h2>',
        f'<p style="margin:0 0 20px;color:#666;font-size:13px">{today} · 신규 {len(items)}건</p>',
    ]

    for category, group in group_by_category(items).items():
        parts.append(
            f'<h3 style="margin:22px 0 8px;padding-bottom:6px;border-bottom:2px solid #e3e3e3;'
            f'font-size:15px">{escape(category)} '
            f'<span style="color:#888;font-weight:normal">{len(group)}건</span></h3>'
        )
        parts.append('<table style="width:100%;border-collapse:collapse;font-size:14px">')
        for item in group:
            date = escape(item.published or "—")
            parts.append(
                '<tr>'
                f'<td style="padding:8px 10px 8px 0;border-bottom:1px solid #f0f0f0;'
                f'white-space:nowrap;vertical-align:top;color:#888;font-size:12px">{date}</td>'
                f'<td style="padding:8px 10px 8px 0;border-bottom:1px solid #f0f0f0;'
                f'white-space:nowrap;vertical-align:top;color:#555;font-size:12px">'
                f'{escape(item.source_name)}</td>'
                f'<td style="padding:8px 0;border-bottom:1px solid #f0f0f0;vertical-align:top">'
                f'<a href="{escape(item.url)}" style="color:#1155cc;text-decoration:none">'
                f'{escape(item.title)}</a></td>'
                '</tr>'
            )
        parts.append("</table>")

    if errors:
        parts.append(
            '<h3 style="margin:26px 0 8px;font-size:14px;color:#a33">수집 실패 소스 '
            '<span style="font-weight:normal;color:#888">(설정 점검 필요)</span></h3>'
            '<ul style="margin:0;padding-left:18px;color:#a33;font-size:12px">'
            + "".join(f"<li>{escape(e)}</li>" for e in errors)
            + "</ul>"
        )

    parts.append(
        '<p style="margin-top:28px;color:#999;font-size:11px">'
        'research/sources.json 에서 대상 사이트와 키워드를 조정할 수 있습니다. '
        '수집이 실패하는 소스는 국내 PC에서 <code>python3 research/discover.py --fix</code> 로 점검하세요.</p>'
        "</div>"
    )
    return "".join(parts)


def send_email(items: list[Item], errors: list[str], gmail_addr: str,
               gmail_pass: str, to_addrs: list[str]) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = build_subject(items)
    msg["From"] = gmail_addr
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(build_text(items, errors), "plain", "utf-8"))
    msg.attach(MIMEText(build_html(items, errors), "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(gmail_addr, gmail_pass)
        server.sendmail(gmail_addr, to_addrs, msg.as_string())
