"""
Lambda 핸들러 동작 테스트. boto3와 SMTP를 가짜로 바꿔 끼워 네트워크 없이 검증한다.

확인 항목
  - 최초 실행은 기준선만 저장하고 메일을 보내지 않는다
  - 두 번째 실행에서 신규 자료가 없으면 메일을 보내지 않는다
  - 새 자료가 생기면 그 건만 메일로 나간다

실행: python3 tests/test_handler.py
"""

import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "research"))
sys.path.insert(0, os.path.join(ROOT, "lambda"))

FAILURES = []


def check(label, actual, expected):
    if actual != expected:
        FAILURES.append(f"{label}\n    기대: {expected!r}\n    실제: {actual!r}")


# --- 가짜 boto3 / S3 --------------------------------------------------------
class _NoSuchKey(Exception):
    pass


class _FakeS3:
    exceptions = types.SimpleNamespace(NoSuchKey=_NoSuchKey)

    def __init__(self):
        self.store = {}

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 시그니처를 그대로 흉내낸다
        if Key not in self.store:
            raise _NoSuchKey(Key)
        return {"Body": types.SimpleNamespace(read=lambda: self.store[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803
        self.store[Key] = Body


fake_s3 = _FakeS3()
fake_boto3 = types.ModuleType("boto3")
fake_boto3.client = lambda name, **kw: fake_s3
sys.modules["boto3"] = fake_boto3

import collector  # noqa: E402
import digest  # noqa: E402

os.environ.update({
    "GMAIL_ADDRESS": "test@example.com",
    "GMAIL_APP_PASSWORD": "x" * 16,
    "ALERT_TO": "a@example.com, b@example.com",
    "STATE_BUCKET": "fake-bucket",
})

import research_handler  # noqa: E402

# --- 가짜 수집 결과 ---------------------------------------------------------
PAGE_V1 = """<html><body>
  <tr><td><a href="/v.do?id=1">인공지능 규제 동향 보고서</a></td><td>2026.09.10</td></tr>
</body></html>"""
PAGE_V2 = """<html><body>
  <tr><td><a href="/v.do?id=2">AI 기본법 시행령 분석</a></td><td>2026.09.11</td></tr>
  <tr><td><a href="/v.do?id=1">인공지능 규제 동향 보고서</a></td><td>2026.09.10</td></tr>
</body></html>"""

state = {"page": PAGE_V1}
collector.fetch = lambda url, timeout=20: state["page"]
collector.load_config = lambda path=None: {
    "max_age_days": 0,   # 테스트 날짜에 의존하지 않도록 기간 필터를 끈다
    "sources": [{"id": "fake", "name": "가상연구원", "category": "연구기관", "type": "html",
                 "url": "https://fake.re.kr/list", "base_url": "https://fake.re.kr",
                 "link_pattern": "v\\.do"}],
}

sent = []
digest.send_email = lambda items, errors, addr, pw, to: sent.append((items, to))

# --- 1회차 ------------------------------------------------------------------
r1 = research_handler.handler({}, None)
check("1회차: 메일 미발송", len(sent), 0)
check("1회차: 기준선 저장 메시지", "최초 실행" in r1["body"], True)
check("1회차: 상태 파일 기록", json.loads(fake_s3.store["seen_research.json"]) != {}, True)

# --- 2회차 (변화 없음) --------------------------------------------------------
r2 = research_handler.handler({}, None)
check("2회차: 신규 없음", r2["body"], "새로운 자료 없음")
check("2회차: 메일 미발송", len(sent), 0)

# --- 3회차 (새 자료 1건) -------------------------------------------------------
state["page"] = PAGE_V2
r3 = research_handler.handler({}, None)
check("3회차: 신규 1건", r3["new"], 1)
check("3회차: 메일 1통 발송", len(sent), 1)
check("3회차: 새로 올라온 자료만 포함", [i.title for i in sent[0][0]], ["AI 기본법 시행령 분석"])
check("3회차: 수신자 콤마 분리", sent[0][1], ["a@example.com", "b@example.com"])

# --- 4회차 (다시 변화 없음) -----------------------------------------------------
r4 = research_handler.handler({}, None)
check("4회차: 같은 자료 재발송 안 함", len(sent), 1)
check("4회차: 수집 건수 유지", r4["collected"], 2)

if FAILURES:
    print(f"실패 {len(FAILURES)}건\n")
    for f in FAILURES:
        print("  ✗ " + f)
    sys.exit(1)
print("모든 테스트 통과")
