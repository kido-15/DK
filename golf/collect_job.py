"""웹 대시보드에서 누른 순간 수집을 시작하는 백그라운드 작업 관리자.

검색한 날짜가 스냅샷에 없을 때, 터미널에서 scripts/collect_full.py 를
따로 실행하는 대신 화면에서 "지금 모으기" 를 누르면 이 모듈이 백그라운드
스레드로 그 날짜만 모은다. 하루치가 아니라 **딱 그 날짜만** 모으는 것이
핵심이다 — 검색할 때마다 여러 날을 다시 훑을 이유가 없다.

한 소스당 동시에 하나만 돈다. 여러 탭에서 동시에 눌러도 사이트를 여러 번
두드리지 않고, 이미 도는 작업의 상태를 그대로 보여준다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from . import collect


@dataclass
class JobStatus:
    source_id: str
    status: str = "idle"        # idle | running | done | error
    dates: list = field(default_factory=list)
    current_date: str = ""
    current_bucket: str = ""    # 지역별로 나눠 받는 소스는 지금 어느 묶음인지
    page: int = 0
    rows_so_far: int = 0
    requests_so_far: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""
    result_rows: int = 0

    def to_dict(self) -> dict:
        if self.started_at:
            end = self.finished_at or time.time()
            elapsed = round(end - self.started_at, 1)
        else:
            elapsed = 0.0
        return {
            "source": self.source_id,
            "status": self.status,
            "dates": self.dates,
            "current_date": self.current_date,
            "current_bucket": self.current_bucket,
            "page": self.page,
            "rows_so_far": self.rows_so_far,
            "requests_so_far": self.requests_so_far,
            "elapsed_sec": elapsed,
            "error": self.error,
            "result_rows": self.result_rows,
        }


class CollectJobManager:
    """소스별로 백그라운드 수집 작업을 하나씩만 돌린다."""

    def __init__(self, *, runner=None):
        self._lock = threading.Lock()
        self._jobs: dict[str, JobStatus] = {}
        # 시험에서 실제 스레드/네트워크 없이 흉내 낼 수 있게 주입 지점을 둔다.
        self._runner = runner or self._default_runner

    def status(self, source_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(source_id)
        return job.to_dict() if job else JobStatus(source_id=source_id).to_dict()

    def is_running(self, source_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(source_id)
        return bool(job and job.status == "running")

    def start(self, source_id: str, dates: list[date], *,
             config_path: str = "") -> dict:
        """이미 그 소스가 돌고 있으면 새로 시작하지 않고 지금 상태를 돌려준다."""
        with self._lock:
            existing = self._jobs.get(source_id)
            if existing and existing.status == "running":
                return {"started": False, "already_running": True,
                       "status": existing.to_dict()}
            job = JobStatus(source_id=source_id, status="running",
                           dates=[d.isoformat() for d in dates],
                           started_at=time.time())
            self._jobs[source_id] = job

        thread = threading.Thread(
            target=self._run_job, args=(job, dates, config_path), daemon=True)
        thread.start()
        return {"started": True, "status": job.to_dict()}

    def _run_job(self, job: JobStatus, dates: list[date], config_path: str) -> None:
        def on_progress(d, page, rows, total, bucket=""):
            with self._lock:
                job.current_date = d.isoformat()
                job.current_bucket = bucket
                job.page = page
                job.rows_so_far = total

        try:
            result_rows, requests_made = self._runner(
                job.source_id, dates, config_path, on_progress)
            with self._lock:
                job.status = "done"
                job.result_rows = result_rows
                job.requests_so_far = requests_made
                job.finished_at = time.time()
        except Exception as exc:  # 백그라운드 스레드라 예외를 잡아 상태에 남긴다
            with self._lock:
                job.status = "error"
                job.error = str(exc)[:300]
                job.finished_at = time.time()

    @staticmethod
    def _default_runner(source_id, dates, config_path, on_progress):
        """실제로 사이트를 훑어 스냅샷에 저장한다."""
        src = collect.build_source(source_id, config_path=config_path)
        rows = src.fetch(dates, on_progress=on_progress)
        stats = src.last_stats
        collect.merge_into_snapshot(source_id, rows, dates,
                                    requests=stats.get("requests", 0))
        return len(rows), stats.get("requests", 0)
