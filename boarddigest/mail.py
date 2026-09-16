"""알림 메일 본문 구성 및 Gmail SMTP 발송.

기존 ai-bill-alert와 같은 방식(앱 비밀번호 + SMTP_SSL 465)을 쓴다.
"""

from __future__ import annotations

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from .dates import format_kr
from .runner import DigestReport, SiteReport

SUBJECT_PREFIX = "[연구기관 자료 알림]"


def build_subject(report: DigestReport) -> str:
    count = len(report.items)
    if count:
        return f"{SUBJECT_PREFIX} {report.target_label} 신규 자료 {count}건"
    if report.failed:
        return f"{SUBJECT_PREFIX} {report.target_label} 수집 오류 {len(report.failed)}건"
    return f"{SUBJECT_PREFIX} {report.target_label} 신규 자료 없음"


def _site_lines(site: SiteReport) -> list[str]:
    lines = [f"■ {site.site_name} ({len(site.items)}건)"]
    for item in site.items:
        date_label = format_kr(item.posted) if item.posted else item.raw_date
        prefix = f"[{item.category}] " if item.category else ""
        lines.append(f"  - {prefix}{item.title}  ({date_label})")
        if item.url:
            lines.append(f"    {item.url}")
    return lines


def build_text(report: DigestReport) -> str:
    blocks: list[str] = []
    if report.items:
        blocks.append(f"{report.target_label}에 등록된 신규 자료 {len(report.items)}건입니다.")
    else:
        blocks.append(f"{report.target_label} 기준 신규 자료가 없습니다.")

    for site in report.sites:
        if site.items:
            blocks.append("\n".join(_site_lines(site)))

    quiet = [s for s in report.sites if s.ok and not s.items]
    if quiet:
        blocks.append("신규 없음: " + ", ".join(s.site_name for s in quiet))

    if report.failed:
        failed_lines = ["⚠ 수집 실패 (게시판 구조 변경 또는 접속 오류)"]
        for site in report.failed:
            failed_lines.append(f"  - {site.site_name}: {site.error}")
            failed_lines.append(f"    {site.list_url}")
        blocks.append("\n".join(failed_lines))

    warned = [s for s in report.sites if s.ok and s.warnings]
    if warned:
        warn_lines = ["· 참고 사항"]
        for site in warned:
            for warning in site.warnings:
                warn_lines.append(f"  - {site.site_name}: {warning}")
        blocks.append("\n".join(warn_lines))

    return "\n\n".join(blocks) + "\n"


def build_html(report: DigestReport) -> str:
    parts = [
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Malgun Gothic\','
        "'Apple SD Gothic Neo',sans-serif;font-size:14px;line-height:1.6;color:#1a1a1a;"
        'max-width:760px">'
    ]
    if report.items:
        parts.append(
            f'<p style="margin:0 0 18px"><strong>{escape(report.target_label)}</strong>에 등록된 '
            f"신규 자료 <strong>{len(report.items)}</strong>건입니다.</p>"
        )
    else:
        parts.append(
            f'<p style="margin:0 0 18px">{escape(report.target_label)} 기준 신규 자료가 없습니다.</p>'
        )

    for site in report.sites:
        if not site.items:
            continue
        parts.append(
            f'<h3 style="margin:22px 0 8px;font-size:15px;border-left:3px solid #2f6fdb;'
            f'padding-left:8px">{escape(site.site_name)} '
            f'<span style="color:#666;font-weight:normal">{len(site.items)}건</span></h3>'
        )
        parts.append('<ul style="margin:0;padding-left:18px">')
        for item in site.items:
            date_label = format_kr(item.posted) if item.posted else item.raw_date
            category = (
                f'<span style="color:#2f6fdb">[{escape(item.category)}]</span> '
                if item.category else ""
            )
            title = escape(item.title)
            linked = (
                f'<a href="{escape(item.url, quote=True)}" style="color:#1a1a1a;'
                f'text-decoration:underline">{title}</a>'
                if item.url else title
            )
            parts.append(
                f'<li style="margin-bottom:6px">{category}{linked}'
                f'<span style="color:#888;font-size:12px"> · {escape(date_label)}</span></li>'
            )
        parts.append("</ul>")

    quiet = [s for s in report.sites if s.ok and not s.items]
    if quiet:
        parts.append(
            '<p style="margin:20px 0 0;color:#888;font-size:12px">신규 없음: '
            + escape(", ".join(s.site_name for s in quiet))
            + "</p>"
        )

    if report.failed:
        parts.append(
            '<div style="margin-top:20px;padding:10px 12px;background:#fff6f6;'
            'border:1px solid #f0c9c9;border-radius:4px;font-size:12px">'
            "<strong>⚠ 수집 실패</strong><ul style=\"margin:6px 0 0;padding-left:18px\">"
        )
        for site in report.failed:
            parts.append(
                f"<li>{escape(site.site_name)}: {escape(site.error)}<br>"
                f'<a href="{escape(site.list_url, quote=True)}" style="color:#888">'
                f"{escape(site.list_url)}</a></li>"
            )
        parts.append("</ul></div>")

    warned = [s for s in report.sites if s.ok and s.warnings]
    if warned:
        parts.append(
            '<div style="margin-top:12px;color:#888;font-size:12px">'
            "<strong>참고</strong><ul style=\"margin:6px 0 0;padding-left:18px\">"
        )
        for site in warned:
            for warning in site.warnings:
                parts.append(f"<li>{escape(site.site_name)}: {escape(warning)}</li>")
        parts.append("</ul></div>")

    parts.append("</div>")
    return "".join(parts)


def send_email(
    report: DigestReport,
    *,
    gmail_address: str,
    gmail_app_password: str,
    to_addrs: list[str],
    subject: str | None = None,
) -> str:
    subject = subject or build_subject(report)
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = gmail_address
    message["To"] = ", ".join(to_addrs)
    message.attach(MIMEText(build_text(report), "plain", "utf-8"))
    message.attach(MIMEText(build_html(report), "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(gmail_address, gmail_app_password)
        server.sendmail(gmail_address, to_addrs, message.as_string())
    return subject
