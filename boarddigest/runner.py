"""사이트를 돌며 '지정한 날짜(기본: 전날)'에 올라온 글만 모은다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import enabled_sites
from .dates import format_kr, today_kst, yesterday_kst
from .fetch import FetchError, fetch_text
from .parse import Item, ParseResult, parse_board, parse_feed
from .state import BaseStore


@dataclass
class SiteReport:
    site_id: str
    site_name: str
    list_url: str
    items: list[Item] = field(default_factory=list)
    scanned: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class DigestReport:
    target_dates: list[date]
    sites: list[SiteReport] = field(default_factory=list)
    # 발송이 성공한 뒤에 '보냄'으로 기록할 항목 키 (commit_state 참고)
    pending_keys: list[str] = field(default_factory=list)

    @property
    def items(self) -> list[Item]:
        return [item for site in self.sites for item in site.items]

    @property
    def failed(self) -> list[SiteReport]:
        return [s for s in self.sites if not s.ok]

    @property
    def target_label(self) -> str:
        if len(self.target_dates) == 1:
            return format_kr(self.target_dates[0])
        return f"{format_kr(min(self.target_dates))} ~ {format_kr(max(self.target_dates))}"


def target_dates_for(days: int = 1, end: date | None = None) -> list[date]:
    """기본값은 '전날 하루'. days=3이면 전날부터 3일치."""
    end = end or yesterday_kst()
    return [end - timedelta(days=offset) for offset in range(days)]


def _keyword_ok(title: str, include: list[str], exclude: list[str]) -> bool:
    lowered = title.lower()
    if include and not any(k.lower() in lowered for k in include):
        return False
    return not any(k.lower() in lowered for k in exclude if k)


def collect_site(site: dict, targets: set[date]) -> SiteReport:
    report = SiteReport(site["id"], site["name"], site["list_url"])
    parsed_all: list[Item] = []

    for offset in range(site["pages"]):
        params = dict(site.get("params") or {})
        if site["pages"] > 1:
            params[site["page_param"]] = site["page_start"] + offset
        try:
            text = fetch_text(
                site["list_url"],
                params=params or None,
                headers=site.get("headers"),
                encoding=site.get("encoding"),
                timeout=int(site.get("timeout", 20)),
                retries=int(site.get("retries", 3)),
                data=site.get("data") or None,
            )
        except FetchError as e:
            report.error = str(e)
            return report

        result: ParseResult = (
            parse_feed(text, site) if site.get("type") == "rss"
            else parse_board(text, site["list_url"], site)
        )
        parsed_all.extend(result.items)
        for warning in result.warnings:
            if warning not in report.warnings:
                report.warnings.append(warning)
        if result.detected:
            hint = " / ".join(f"{k}: {v}" for k, v in result.detected.items() if v)
            note = f"자동탐지로 읽었습니다. 설정에 넣어두면 안정적입니다 -> {hint}"
            if note not in report.warnings:
                report.warnings.append(note)

    report.scanned = len(parsed_all)
    include = site.get("include_keywords") or []
    exclude = site.get("exclude_keywords") or []
    seen_keys: set[str] = set()
    for item in parsed_all:
        if item.posted not in targets:
            continue
        if not _keyword_ok(item.title, include, exclude):
            continue
        if item.key() in seen_keys:
            continue
        seen_keys.add(item.key())
        report.items.append(item)
    report.items.sort(key=lambda i: (i.posted or date.min, i.title), reverse=True)
    return report


def run_digest(
    config: dict,
    store: BaseStore,
    *,
    only: list[str] | None = None,
    days: int = 1,
    end: date | None = None,
    use_state: bool = True,
) -> DigestReport:
    targets = target_dates_for(days=days, end=end)
    report = DigestReport(target_dates=targets)
    target_set = set(targets)

    for site in enabled_sites(config, only):
        report.sites.append(collect_site(site, target_set))

    if not use_state:
        return report

    seen = store.load()
    for site_report in report.sites:
        kept: list[Item] = []
        for item in site_report.items:
            if item.key() in seen:
                continue  # 이미 보낸 글
            kept.append(item)
            report.pending_keys.append(item.key())
        site_report.items = kept

    return report


def commit_state(store: BaseStore, report: DigestReport) -> int:
    """메일 발송이 성공한 뒤에만 호출한다.

    발송 전에 기록해 버리면, SMTP 오류로 메일이 실패했을 때 그 자료를
    영구히 놓치게 된다(다음 실행에서 '이미 보낸 글'로 걸러지므로).
    """
    if not report.pending_keys:
        return 0
    seen = store.load()
    stamp = today_kst().isoformat()
    for key in report.pending_keys:
        seen[key] = stamp
    store.save(store.prune(seen, today_kst()))
    return len(report.pending_keys)
