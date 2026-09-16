"""boarddigest 단위/통합 테스트.

실행: python3 -m unittest discover -s tests -v
통합 테스트는 localhost에 임시 HTTP 서버를 띄워 실제 수집 경로까지 확인한다.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
from datetime import date, timedelta
from email import message_from_string
from email.header import decode_header, make_header
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boarddigest.config import ConfigError, normalize  # noqa: E402
from boarddigest.dates import format_kr, parse_date, yesterday_kst  # noqa: E402
from boarddigest.dom import parse_html  # noqa: E402
from boarddigest.mail import build_html, build_subject, build_text  # noqa: E402
from boarddigest.parse import (  # noqa: E402
    autodetect_rows, js_call_args, js_call_name, parse_board, parse_feed,
)
from boarddigest.runner import commit_state, run_digest, target_dates_for  # noqa: E402
from boarddigest.state import MemoryStore  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
TODAY = date(2026, 9, 16)
YESTERDAY = date(2026, 9, 15)


class TestDom(unittest.TestCase):
    def test_implied_end_tags(self):
        doc = parse_html("<table><tr><td>가<td>나<tr><td>다</table>")
        rows = doc.select("table tr")
        self.assertEqual(len(rows), 2)
        self.assertEqual([c.text() for c in rows[0].select("td")], ["가", "나"])

    def test_selector_forms(self):
        html = (
            '<div id="wrap"><ul class="a b"><li class="item x"><a href="/1" title="t">글1</a></li>'
            '<li class="item"><span>글2</span></li></ul></div>'
        )
        doc = parse_html(html)
        self.assertEqual(len(doc.select("ul.a li.item")), 2)
        self.assertEqual(len(doc.select("#wrap > ul > li.x")), 1)
        self.assertEqual(len(doc.select("a[href^=/1]")), 1)
        self.assertEqual(len(doc.select("li[class*=x]")), 1)
        self.assertEqual(len(doc.select("a[title=t]")), 1)
        self.assertEqual(len(doc.select("span, a")), 2)
        self.assertEqual(doc.select_one("a").get("href"), "/1")

    def test_entities_and_script_text(self):
        doc = parse_html("<p>AI &amp; 규제<script>var x='숨김';</script></p>")
        self.assertEqual(doc.select_one("p").text(), "AI & 규제")

    def test_text_keeps_document_order(self):
        # <td>앞<b>가운데</b>뒤</td> 를 "앞 뒤 가운데"로 읽으면 제목이 조용히 뒤집힌다
        doc = parse_html("<td>앞<b>가운데</b>뒤</td>")
        self.assertEqual(doc.select_one("td").text(), "앞 가운데 뒤")
        doc = parse_html('<strong><span class="notice">공지</span> 실제 제목</strong>')
        self.assertEqual(doc.select_one("strong").text(), "공지 실제 제목")

    def test_direct_text_excludes_child_labels(self):
        doc = parse_html("<li><strong>등록일</strong> 2026.09.15</li>")
        self.assertEqual(doc.select_one("li").direct_text(), "2026.09.15")
        self.assertEqual(doc.select_one("li").text(), "등록일 2026.09.15")

    def test_unbalanced_close_tag_ignored(self):
        doc = parse_html("<div><p>본문</div></p><div>둘째</div>")
        self.assertEqual(len(doc.select("div")), 2)


class TestDates(unittest.TestCase):
    def test_formats(self):
        cases = {
            "2026-09-15": date(2026, 9, 15),
            "2026.09.15.": date(2026, 9, 15),
            "2026년 9월 15일": date(2026, 9, 15),
            "26.09.15": date(2026, 9, 15),
            "20260915": date(2026, 9, 15),
            "등록일 2026.09.15 | 조회 88": date(2026, 9, 15),
            "어제": YESTERDAY,
            "오늘": TODAY,
            "3일 전": date(2026, 9, 13),
            "14:30": TODAY,
        }
        for text, expected in cases.items():
            self.assertEqual(parse_date(text, today=TODAY), expected, text)

    def test_invalid(self):
        for text in ["", "조회 1234", "2026.13.45", "제목만 있음"]:
            self.assertIsNone(parse_date(text, today=TODAY), text)

    def test_explicit_format_wins(self):
        self.assertEqual(parse_date("15/09/2026", explicit_format="%d/%m/%Y"), date(2026, 9, 15))

    def test_require_year_skips_bare_month_day(self):
        # 제목 안을 뒤질 때 "3/4분기" 같은 표현을 날짜로 오인하면 안 된다
        self.assertIsNone(parse_date("AI 기본법 3/4분기 점검", require_year=True))
        self.assertIsNotNone(parse_date("AI 기본법 3/4분기 점검"))
        self.assertEqual(parse_date("2026.09.15", require_year=True), date(2026, 9, 15))

    def test_month_day_rolls_back_a_year(self):
        self.assertEqual(parse_date("12-31", today=TODAY), date(2025, 12, 31))


class TestParse(unittest.TestCase):
    def setUp(self):
        self.table_html = (FIXTURES / "board_table.html").read_text(encoding="utf-8")
        self.list_html = (FIXTURES / "board_list.html").read_text(encoding="utf-8")
        self.onclick_html = (FIXTURES / "board_onclick.html").read_text(encoding="utf-8")

    def test_table_with_selectors(self):
        result = parse_board(self.table_html, "https://demo.re.kr/board/list.do", {
            "id": "demo", "name": "데모",
            "row_selector": "table.board_list tbody tr",
            "title_selector": "td.subject a",
            "date_selector": "td.date",
        })
        self.assertEqual(result.warnings, [])
        self.assertEqual(len(result.items), 3)  # 공지 행은 제외
        self.assertEqual(result.items[0].posted, YESTERDAY)
        self.assertEqual(result.items[0].url, "https://demo.re.kr/board/view.do?idx=142")
        self.assertNotIn("NEW", result.items[0].title)

    def test_autodetect_suggests_selectors(self):
        result = parse_board(self.table_html, "https://demo.re.kr/board/list.do",
                             {"id": "demo", "name": "데모"})
        self.assertEqual(len(result.items), 3)
        self.assertEqual(result.detected["row_selector"], "table.board_list tbody tr")
        self.assertEqual(result.detected["title_selector"], "td.subject a")
        self.assertEqual(result.detected["date_selector"], "td.date")

    def test_paging_links_are_not_items(self):
        result = parse_board(self.table_html, "https://demo.re.kr/list.do", {"id": "d", "name": "d"})
        self.assertFalse([i for i in result.items if i.title in ("다음", "1", "2")])

    def test_javascript_link_template(self):
        result = parse_board(self.list_html, "https://demo.kr/list.do", {
            "id": "spri", "name": "데모",
            "row_selector": "ul.bbs-list li.item",
            "title_selector": "a.tit",
            "date_selector": "span.info",
            "category_selector": "span.cate",
            "detail_url_template": "https://demo.kr/view.do?date={arg0}&no={arg1}",
        })
        self.assertEqual(result.items[0].url, "https://demo.kr/view.do?date=20260915&no=IS-171")
        self.assertEqual(result.items[0].category, "이슈리포트")

    def test_javascript_link_without_template_warns(self):
        result = parse_board(self.list_html, "https://demo.kr/list.do", {"id": "s", "name": "s"})
        self.assertTrue(any("detail_url_template" in w for w in result.warnings))

    def test_onclick_link_with_selectors(self):
        # 국내 기관 게시판에 흔한 href="#none" onclick="goView('115116','')" 구조
        result = parse_board(self.onclick_html, "https://demo.re.kr/bbs/list.do", {
            "id": "demo", "name": "데모",
            "row_selector": "div.board_list > ul > li",
            "title_selector": "a > strong",
            "link_selector": "a",
            "date_selector": "a ul li",
            "detail_url_template": "https://demo.re.kr/bbs/view.do?key=abc&bbsSn={arg0}",
        })
        self.assertEqual(result.warnings, [])
        self.assertEqual(len(result.items), 3)  # class="notice" 고정 공지는 제외
        self.assertEqual(result.items[0].title, "AI 기본법 3/4분기 이행점검 결과")
        self.assertEqual(result.items[0].posted, YESTERDAY)
        self.assertEqual(
            result.items[0].url,
            "https://demo.re.kr/bbs/view.do?key=abc&bbsSn=115116",
        )

    def test_onclick_board_autodetects_rows(self):
        # 등록일이 제목 <a> 안쪽에 있고, 푸터에도 날짜가 있는 구조
        result = parse_board(self.onclick_html, "https://demo.re.kr/bbs/list.do",
                             {"id": "demo", "name": "데모"})
        self.assertEqual(len(result.items), 3)
        self.assertNotIn("footer", result.detected["row_selector"])
        self.assertEqual([i.posted for i in result.items],
                         [YESTERDAY, YESTERDAY - timedelta(days=1), YESTERDAY - timedelta(days=2)])

    def test_onclick_warning_names_the_function(self):
        result = parse_board(self.onclick_html, "https://demo.re.kr/bbs/list.do",
                             {"id": "demo", "name": "데모"})
        self.assertTrue(any("goView" in w and "115116" in w for w in result.warnings))

    def test_js_call_helpers(self):
        anchor = parse_html("<a href=\"#none\" onclick=\"goView('115116', '');\">글</a>").select_one("a")
        self.assertEqual(js_call_args("#none", anchor), ["115116"])
        self.assertEqual(js_call_name(anchor), "goView")
        plain = parse_html('<a href="/view.do?idx=1">글</a>').select_one("a")
        self.assertEqual(js_call_args("/view.do?idx=1", plain), [])
        self.assertEqual(js_call_name(plain), "")

    def test_detail_url_template_missing_arg_is_not_fatal(self):
        # 템플릿이 {arg1}을 참조하는데 인자가 하나뿐이면, 링크만 비우고 수집은 계속한다
        result = parse_board(self.onclick_html, "https://demo.re.kr/bbs/list.do", {
            "id": "demo", "name": "데모",
            "row_selector": "div.board_list > ul > li",
            "title_selector": "a > strong",
            "link_selector": "a",
            "date_selector": "a ul li",
            "detail_url_template": "https://demo.re.kr/view.do?a={arg0}&b={arg1}",
        })
        self.assertEqual(len(result.items), 3)
        self.assertEqual(result.items[0].url, "")

    def test_bad_row_selector_warns(self):
        result = parse_board(self.table_html, "https://demo.re.kr/list.do", {
            "id": "d", "name": "d", "row_selector": "table.does-not-exist tr",
        })
        self.assertEqual(result.items, [])
        self.assertTrue(any("row_selector" in w for w in result.warnings))

    def test_empty_and_garbage_html(self):
        for html in ["", "<html><body><p>목록이 없습니다</p></body></html>", "{\"json\": true}"]:
            result = parse_board(html, "https://demo.re.kr/list.do", {"id": "d", "name": "d"})
            self.assertEqual(result.items, [])
            self.assertTrue(result.warnings)

    def test_rss(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><title>보도자료</title>
        <item><title>AI 기본법 시행령 의결</title><link>https://go.kr/p/1</link>
              <pubDate>Tue, 15 Sep 2026 10:00:00 +0900</pubDate></item>
        <item><title>주파수 재할당 계획</title><link>https://go.kr/p/2</link>
              <pubDate>Mon, 14 Sep 2026 09:00:00 +0900</pubDate></item>
        </channel></rss>"""
        result = parse_feed(xml, {"id": "kcc", "name": "보도자료", "list_url": "https://go.kr/rss"})
        self.assertEqual(len(result.items), 2)
        self.assertEqual(result.items[0].posted, YESTERDAY)
        self.assertEqual(result.items[0].url, "https://go.kr/p/1")

    def test_atom_feed(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>보고서 공개</title><link href="https://x.kr/a"/>
        <updated>2026-09-15T01:00:00Z</updated></entry></feed>"""
        result = parse_feed(xml, {"id": "x", "name": "x", "list_url": "https://x.kr/feed"})
        self.assertEqual(result.items[0].posted, YESTERDAY)  # UTC -> KST 변환

    def test_autodetect_needs_repetition(self):
        rows, _ = autodetect_rows(parse_html('<div><a href="/1">글</a><span>2026-09-15</span></div>'))
        self.assertEqual(rows, [])


class TestConfig(unittest.TestCase):
    def test_defaults_and_keyword_merge(self):
        config = normalize({
            "defaults": {"pages": 2},
            "exclude_keywords": ["채용"],
            "sites": [{"id": "a", "name": "A", "list_url": "https://a.kr",
                       "exclude_keywords": ["입찰"]}],
        })
        site = config["sites"][0]
        self.assertEqual(site["pages"], 2)
        self.assertEqual(sorted(site["exclude_keywords"]), ["입찰", "채용"])
        self.assertTrue(site["enabled"])

    def test_missing_required_field(self):
        with self.assertRaises(ConfigError):
            normalize({"sites": [{"id": "a", "name": "A"}]})

    def test_duplicate_id(self):
        with self.assertRaises(ConfigError):
            normalize({"sites": [
                {"id": "a", "name": "A", "list_url": "https://a.kr"},
                {"id": "a", "name": "B", "list_url": "https://b.kr"},
            ]})

    def test_list_form_accepted(self):
        config = normalize([{"id": "a", "name": "A", "list_url": "https://a.kr"}])
        self.assertEqual(len(config["sites"]), 1)

    def test_shipped_sites_json_is_valid(self):
        from boarddigest.config import load_config
        for name in ("sites.json", "sites.example.json"):
            self.assertIsNotNone(load_config(ROOT / name))


class TestTargetDates(unittest.TestCase):
    def test_single_day_is_yesterday(self):
        self.assertEqual(target_dates_for(1, end=YESTERDAY), [YESTERDAY])

    def test_multi_day_range(self):
        self.assertEqual(
            target_dates_for(3, end=YESTERDAY),
            [YESTERDAY, date(2026, 9, 14), date(2026, 9, 13)],
        )


class TestEndToEnd(unittest.TestCase):
    """실제 HTTP 수집 -> 파싱 -> 전날 필터 -> 중복제거 -> 메일 본문 생성까지."""

    @classmethod
    def setUpClass(cls):
        handler = partial(_QuietHandler, directory=str(FIXTURES))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _config(self):
        base = f"http://127.0.0.1:{self.port}"
        return normalize({"sites": [
            {"id": "table_board", "name": "데모연구원 연구보고서",
             "list_url": f"{base}/board_table.html",
             "row_selector": "table.board_list tbody tr",
             "title_selector": "td.subject a", "date_selector": "td.date"},
            {"id": "list_board", "name": "데모원 이슈리포트",
             "list_url": f"{base}/board_list.html",
             "row_selector": "ul.bbs-list li.item", "title_selector": "a.tit",
             "date_selector": "span.info",
             "detail_url_template": "https://demo.kr/view.do?date={arg0}&no={arg1}"},
            {"id": "dead_site", "name": "응답없는 기관",
             "list_url": f"{base}/no-such-page.html", "retries": 1},
        ]})

    def test_yesterday_only_and_dedup(self):
        store = MemoryStore()
        report = run_digest(self._config(), store, days=1, end=YESTERDAY)

        titles = [i.title for i in report.items]
        self.assertEqual(len(titles), 3)  # 09-15 자료만 (표 2건 + 리스트 1건)
        self.assertTrue(all(i.posted == YESTERDAY for i in report.items))
        self.assertIn("AI 거버넌스 국내외 동향 2026", titles)
        self.assertNotIn("방송시장 경쟁상황 평가 개선방안", titles)  # 09-14
        self.assertNotIn("[공지] 자료 이용 안내", titles)

        self.assertEqual(len(report.failed), 1)
        self.assertEqual(report.failed[0].site_id, "dead_site")
        self.assertIn("404", report.failed[0].error)

        # 발송 성공을 기록하기 전에는 같은 글이 그대로 다시 잡힌다
        retry = run_digest(self._config(), store, days=1, end=YESTERDAY)
        self.assertEqual(len(retry.items), 3, "메일 발송 실패 시 다음 실행에서 재시도되어야 한다")

        # 기록 후에는 다시 잡히지 않는다
        self.assertEqual(commit_state(store, retry), 3)
        again = run_digest(self._config(), store, days=1, end=YESTERDAY)
        self.assertEqual(again.items, [])

    def test_no_state_does_not_record(self):
        store = MemoryStore()
        report = run_digest(self._config(), store, days=1, end=YESTERDAY, use_state=False)
        self.assertEqual(store.load(), {})
        self.assertEqual(report.pending_keys, [])

    def test_mail_bodies(self):
        report = run_digest(self._config(), MemoryStore(), days=2, end=YESTERDAY)
        subject = build_subject(report)
        text = build_text(report)
        html = build_html(report)

        self.assertIn("2026-09-14 ~ 2026-09-15", subject)
        self.assertIn("데모연구원 연구보고서", text)
        self.assertIn("/board/view.do?idx=142", text)  # 목록 URL 기준으로 절대경로화
        self.assertIn("응답없는 기관", text)  # 실패 사이트도 본문에 알린다
        self.assertNotIn("<!DOCTYPE", text)  # 오류 메시지에 HTML 원문이 새지 않는다
        self.assertIn("<a href=", html)
        self.assertIn("EU AI Act", html)

    def test_keyword_filter(self):
        config = self._config()
        for site in config["sites"]:
            site["include_keywords"] = ["AI"]
        report = run_digest(config, MemoryStore(), days=7, end=YESTERDAY)
        self.assertTrue(report.items)
        self.assertTrue(all("ai" in i.title.lower() for i in report.items))


class _FakeS3Exceptions:
    class NoSuchKey(Exception):
        pass


class _FakeS3Client:
    """S3 대신 메모리에 저장하는 테스트용 스텁."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.exceptions = _FakeS3Exceptions()

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 시그니처를 따른다
        if (Bucket, Key) not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.objects[(Bucket, Key)] = Body


class _FakeSMTP:
    sent: list[tuple[str, list[str], str]] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.user = user

    def sendmail(self, sender, to_addrs, message):
        _FakeSMTP.sent.append((sender, to_addrs, message))


class TestLambdaHandler(unittest.TestCase):
    """lambda/board_handler.py를 S3/SMTP 스텁으로 실제 실행해 본다."""

    @classmethod
    def setUpClass(cls):
        # '전날' 날짜를 실제 기준으로 생성해, 날짜가 바뀌어도 테스트가 유지되게 한다
        cls.tmpdir = tempfile.mkdtemp()
        posted = format_kr(yesterday_kst())
        (Path(cls.tmpdir) / "list.html").write_text(
            "<html><head><meta charset='utf-8'></head><body>"
            "<table class='bd'><tbody>"
            f"<tr><td class='subject'><a href='/view.do?id=1'>전날 등록 자료</a></td>"
            f"<td class='date'>{posted}</td></tr>"
            "<tr><td class='subject'><a href='/view.do?id=2'>오래된 자료</a></td>"
            "<td class='date'>2020-01-01</td></tr>"
            "</tbody></table></body></html>",
            encoding="utf-8",
        )
        handler = partial(_QuietHandler, directory=cls.tmpdir)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

        cls.fake_s3 = _FakeS3Client()
        fake_boto3 = types.ModuleType("boto3")
        fake_boto3.client = lambda service: cls.fake_s3
        sys.modules.setdefault("boto3", fake_boto3)

        spec = importlib.util.spec_from_file_location(
            "board_handler", ROOT / "lambda" / "board_handler.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _env(self):
        config = {"sites": [{
            "id": "lambda_demo", "name": "람다 테스트 게시판",
            "list_url": f"http://127.0.0.1:{self.port}/list.html",
            "row_selector": "table.bd tbody tr",
            "title_selector": "td.subject a", "date_selector": "td.date",
        }]}
        return {
            "GMAIL_ADDRESS": "sender@gmail.com",
            "GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop",
            "ALERT_TO": "a@example.com, b@example.com",
            "STATE_BUCKET": "test-bucket",
            "LOOKBACK_DAYS": "1",
            "SITES_CONFIG_JSON": json.dumps(config, ensure_ascii=False),
        }

    def test_sends_once_then_stays_quiet(self):
        _FakeSMTP.sent.clear()
        self.fake_s3.objects.clear()
        with mock.patch.dict(os.environ, self._env(), clear=False),              mock.patch("boarddigest.mail.smtplib.SMTP_SSL", _FakeSMTP):
            first = self.module.handler({}, None)
            second = self.module.handler({}, None)

        self.assertEqual(first["statusCode"], 200)
        self.assertEqual(first["items"], 1)
        self.assertEqual(first["failed"], [])
        self.assertEqual(len(_FakeSMTP.sent), 1, "첫 실행에서 1통만 발송되어야 한다")

        sender, to_addrs, raw = _FakeSMTP.sent[0]
        self.assertEqual(sender, "sender@gmail.com")
        self.assertEqual(to_addrs, ["a@example.com", "b@example.com"])
        message = message_from_string(raw)
        decoded = "".join(
            part.get_payload(decode=True).decode("utf-8") for part in message.walk()
            if part.get_content_maintype() == "text"
        )
        self.assertIn("전날 등록 자료", decoded)
        self.assertNotIn("오래된 자료", decoded)
        self.assertIn("연구기관 자료 알림", str(make_header(decode_header(message["Subject"]))))

        # 두 번째 실행: 같은 글이므로 발송 없음 (S3 상태가 유지됨)
        self.assertEqual(second["items"], 0)
        self.assertEqual(len(_FakeSMTP.sent), 1)
        self.assertTrue(self.fake_s3.objects, "상태가 S3에 저장되어야 한다")

    def test_smtp_failure_leaves_state_uncommitted(self):
        """메일 발송이 실패하면 기록하지 않아, 다음 실행에서 다시 시도한다."""
        class _FailingSMTP(_FakeSMTP):
            def sendmail(self, *args, **kwargs):
                raise OSError("SMTP 연결 실패")

        self.fake_s3.objects.clear()
        with mock.patch.dict(os.environ, self._env(), clear=False), \
             mock.patch("boarddigest.mail.smtplib.SMTP_SSL", _FailingSMTP), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(OSError):
                self.module.handler({}, None)
        self.assertEqual(self.fake_s3.objects, {}, "발송 실패 시 상태가 저장되면 안 된다")

        _FakeSMTP.sent.clear()
        with mock.patch.dict(os.environ, self._env(), clear=False), \
             mock.patch("boarddigest.mail.smtplib.SMTP_SSL", _FakeSMTP), \
             contextlib.redirect_stdout(io.StringIO()):
            retried = self.module.handler({}, None)
        self.assertEqual(retried["items"], 1)
        self.assertEqual(len(_FakeSMTP.sent), 1)

    def test_empty_config_does_not_fail(self):
        env = self._env()
        env["SITES_CONFIG_JSON"] = json.dumps({"sites": []})
        with mock.patch.dict(os.environ, env, clear=False),              mock.patch("boarddigest.mail.smtplib.SMTP_SSL", _FakeSMTP):
            result = self.module.handler({}, None)
        self.assertEqual(result["statusCode"], 200)
        self.assertIn("설정된 사이트가 없습니다", result["body"])

    def test_broken_config_reports_error(self):
        env = self._env()
        env["SITES_CONFIG_JSON"] = "{not json"
        with mock.patch.dict(os.environ, env, clear=False), \
             contextlib.redirect_stdout(io.StringIO()):
            result = self.module.handler({}, None)
        self.assertEqual(result["statusCode"], 500)


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # 테스트 출력을 깨끗하게 유지
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
