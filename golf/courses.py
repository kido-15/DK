"""골프장 마스터 데이터 관리.

예약 사이트가 알려주는 것은 "골프장 이름 + 시간 + 가격"뿐이고 좌표는 없다.
이동 시간을 계산하려면 이름을 좌표로 바꿔야 하는데, 그 대조표가 이 모듈이다.

데이터는 CSV 한 장(data/golf/courses.csv)에 담는다.
scripts/fetch_golf_courses.py 를 국내 PC에서 한 번 실행하면
OpenStreetMap에서 실제 좌표를 받아 이 파일을 만들어 준다.
"""

from __future__ import annotations

import csv
import difflib
import os
from typing import Iterable, Optional

from .models import Course, normalize_course_name

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "golf",
    "courses.csv",
)

FIELDNAMES = [
    "course_id",
    "name",
    "lat",
    "lon",
    "region",
    "address",
    "holes",
    "phone",
    "homepage",
    "source",
    "aliases",
]

# 이름이 정확히 일치하지 않을 때 유사도가 이 값 이상이면 같은 골프장으로 본다.
# 너무 낮추면 "서울CC"와 "남서울CC"를 혼동하므로 보수적으로 잡았다.
FUZZY_THRESHOLD = 0.86


class CourseBook:
    """골프장 목록과 이름 → 골프장 매칭을 담당한다."""

    def __init__(self, courses: Optional[Iterable[Course]] = None):
        self.courses: list[Course] = list(courses or [])
        self._index: dict[str, Course] = {}
        self._unmatched: dict[str, int] = {}   # 매칭 실패한 이름과 횟수
        self.reindex()

    # -- 적재 / 저장 --------------------------------------------------------

    @classmethod
    def load(cls, path: str = DEFAULT_PATH) -> "CourseBook":
        if not os.path.exists(path):
            return cls([])
        courses = []
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if not row.get("name") or not row.get("lat"):
                    continue
                try:
                    courses.append(Course.from_dict(row))
                except (ValueError, KeyError):
                    continue
        return cls(courses)

    def save(self, path: str = DEFAULT_PATH) -> int:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            for c in sorted(self.courses, key=lambda x: (x.region, x.name)):
                d = c.to_dict()
                d["aliases"] = "|".join(c.aliases)
                d["holes"] = c.holes if c.holes is not None else ""
                writer.writerow(d)
        return len(self.courses)

    def reindex(self) -> None:
        self._index = {}
        for c in self.courses:
            for key in c.match_keys():
                if key:
                    self._index.setdefault(key, c)

    def add(self, course: Course) -> None:
        self.courses.append(course)
        for key in course.match_keys():
            if key:
                self._index.setdefault(key, course)

    def __len__(self) -> int:
        return len(self.courses)

    # -- 매칭 ---------------------------------------------------------------

    def match(self, raw_name: str) -> Optional[Course]:
        """예약 사이트가 준 이름으로 골프장을 찾는다.

        1) 정규화 후 완전 일치
        2) 한쪽이 다른 쪽을 포함 (가장 긴 후보 우선)
        3) 유사도 기반 근사 일치
        """
        key = normalize_course_name(raw_name)
        if not key:
            return None

        hit = self._index.get(key)
        if hit:
            return hit

        # 포함 관계. "남서울" 같은 짧은 키가 여러 곳에 걸리는 것을 막기 위해
        # 3글자 이상일 때만 시도하고, 후보가 여럿이면 가장 긴 것을 고른다.
        if len(key) >= 3:
            contains = [
                (k, c) for k, c in self._index.items()
                if len(k) >= 3 and (k in key or key in k)
            ]
            if len(contains) == 1:
                return contains[0][1]
            if contains:
                contains.sort(key=lambda kc: len(kc[0]), reverse=True)
                best_len = len(contains[0][0])
                top = [c for k, c in contains if len(k) == best_len]
                if len(top) == 1:
                    return top[0]

        close = difflib.get_close_matches(key, self._index.keys(), n=1, cutoff=FUZZY_THRESHOLD)
        if close:
            return self._index[close[0]]

        self._unmatched[raw_name] = self._unmatched.get(raw_name, 0) + 1
        return None

    def unmatched_report(self) -> list[tuple[str, int]]:
        """매칭에 실패한 이름 목록. 많이 나오는 순.

        여기에 뜨는 이름을 courses.csv의 aliases 열에 넣어 주면 다음 실행부터
        정상적으로 잡힌다.
        """
        return sorted(self._unmatched.items(), key=lambda kv: kv[1], reverse=True)

    def regions(self) -> list[str]:
        return sorted({c.region for c in self.courses if c.region})
