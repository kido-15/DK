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
import re
import os
from typing import Iterable, Optional

from .geo import PLACE_NAMES
from .models import (Course, name_variants, normalize_course_name,
                     region_hint)


def _place_tokens(course: Course) -> set:
    """이 골프장이 놓인 곳을 가리키는 말들 (시도 + 주소의 시·군·구).

    이름 괄호 안의 표시가 지역인지 코스 구분인지 가릴 때 쓴다.
    "청주"·"안성"은 여기에 나타나고, "레이크"·"동북"은 나타나지 않는다.
    """
    out = set()
    if course.region:
        out.add(course.region)
    for token in re.findall(r"[가-힣]{2,4}(?=[시군구]\s|[시군구]$)", course.address or ""):
        out.add(token)
    for token in re.findall(r"([가-힣]{2,4})[시군구](?:\s|$)", course.address or ""):
        out.add(token)
    return out

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
        self._places = set()
        for c in self.courses:
            for key in c.match_keys():
                if key:
                    self._index.setdefault(key, c)
            self._places.update(_place_tokens(c))

    def add(self, course: Course) -> None:
        self.courses.append(course)
        for key in course.match_keys():
            if key:
                self._index.setdefault(key, course)
        self._places.update(_place_tokens(course))

    def __len__(self) -> int:
        return len(self.courses)

    # -- 매칭 ---------------------------------------------------------------

    def match(self, raw_name: str) -> Optional[Course]:
        """예약 사이트가 준 이름으로 골프장을 찾는다.

        예약 사이트는 이름 뒤에 판매 조건을 붙여 적는다("가평(비공개)").
        골프장 DB 에는 그런 말이 없으므로, 꼬리표를 뗀 이름까지 후보로 본다.
        후보는 models.name_variants 가 만든다.

        1) 모든 후보로 완전 일치
        2) 한쪽이 다른 쪽을 포함 (가장 긴 후보 우선)
        3) 유사도 기반 근사 일치

        확실한 방법을 **모든 후보에 대해** 먼저 써 본 뒤 다음 단계로 간다.
        그러지 않으면 첫 후보의 어림짐작이 뒤 후보의 정확한 일치를 이긴다.
        """
        keys = name_variants(raw_name)
        if not keys:
            return None

        for key in keys:                       # 1) 완전 일치
            hit = self._index.get(key)
            if hit:
                return hit

        # 이름 괄호 안의 표시가 **지역**이면, 그 지역이 아닌 곳에 붙이면 안 된다.
        #
        #     그랜드(청주) → 경남의 '그랜드 골프클럽' 에 붙으면 충북이 경남이 된다
        #
        # 좌표가 틀리는 것은 없는 것보다 나쁘다. 없으면 결과에서 빠지지만,
        # 틀리면 "강남역에서 90분" 자리에 경남 골프장이 자신 있게 올라온다.
        #
        # 다만 (레이크)·(동북) 같은 **코스 구분**도 같은 자리에 적힌다. 둘을
        # 글자만 보고 가를 수 없으므로, 좌표 DB 에 실제 지명으로 나타나는
        # 말일 때만 지역으로 본다.
        hint = region_hint(raw_name)
        if hint and hint not in self._places and hint not in PLACE_NAMES:
            hint = ""

        def allowed(course: Course) -> bool:
            if not hint:
                return True
            return hint in f"{course.name} {course.address} {course.region}"

        for key in keys:                       # 2) 포함 관계
            # "남서울" 같은 짧은 키가 여러 곳에 걸리는 것을 막기 위해
            # 3글자 이상일 때만 시도하고, 후보가 여럿이면 가장 긴 것을 고른다.
            if len(key) < 3:
                continue
            contains = [
                (k, c) for k, c in self._index.items()
                if len(k) >= 3 and (k in key or key in k) and allowed(c)
            ]

            if len(contains) == 1:
                return contains[0][1]
            if contains:
                contains.sort(key=lambda kc: len(kc[0]), reverse=True)
                best_len = len(contains[0][0])
                top = [c for k, c in contains if len(k) == best_len]
                if len(top) == 1:
                    return top[0]

        for key in keys:                       # 3) 근사 일치
            pool = [k for k, c in self._index.items() if allowed(c)]
            close = difflib.get_close_matches(key, pool, n=1, cutoff=FUZZY_THRESHOLD)
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
