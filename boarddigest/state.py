"""이미 메일로 보낸 글을 기억해 중복 발송을 막는다.

로컬 실행은 JSON 파일, Lambda는 S3에 저장한다.
저장 형태: {"항목키": "YYYY-MM-DD(발견일)"} — 오래된 항목은 자동 정리한다.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

KEEP_DAYS = 60


class BaseStore:
    def load(self) -> dict[str, str]:
        raise NotImplementedError

    def save(self, seen: dict[str, str]) -> None:
        raise NotImplementedError

    def prune(self, seen: dict[str, str], today: date) -> dict[str, str]:
        cutoff = today - timedelta(days=KEEP_DAYS)
        kept: dict[str, str] = {}
        for key, value in seen.items():
            try:
                if date.fromisoformat(value) >= cutoff:
                    kept[key] = value
            except (TypeError, ValueError):
                kept[key] = today.isoformat()  # 형식이 깨진 값은 오늘로 갱신
        return kept


class FileStore(BaseStore):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if isinstance(data, list):  # 예전 형식(리스트) 호환
            return {key: "1970-01-01" for key in data}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def save(self, seen: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(seen, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )


class S3Store(BaseStore):
    def __init__(self, bucket: str, key: str = "seen_board_items.json") -> None:
        import boto3  # Lambda 런타임에 기본 포함

        self.client = boto3.client("s3")
        self.bucket = bucket
        self.key = key

    def load(self) -> dict[str, str]:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self.key)
        except self.client.exceptions.NoSuchKey:
            return {}
        try:
            data = json.loads(obj["Body"].read())
        except json.JSONDecodeError:
            return {}
        if isinstance(data, list):
            return {key: "1970-01-01" for key in data}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def save(self, seen: dict[str, str]) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=self.key,
            Body=json.dumps(seen, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
            ContentType="application/json",
        )


class MemoryStore(BaseStore):
    """--no-state 또는 테스트용."""

    def __init__(self, seen: dict[str, str] | None = None) -> None:
        self.seen = dict(seen or {})

    def load(self) -> dict[str, str]:
        return dict(self.seen)

    def save(self, seen: dict[str, str]) -> None:
        self.seen = dict(seen)
