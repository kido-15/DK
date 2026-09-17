#!/usr/bin/env python3
"""골프장 검색 웹 대시보드를 띄운다.

    python3 golf_web.py
    python3 golf_web.py --port 9000
    python3 golf_web.py --teetimes data/golf/teetimes.csv   # CSV를 소스로 추가
"""

from __future__ import annotations

import argparse
import sys

from golf.server import make_state, serve


def main() -> int:
    ap = argparse.ArgumentParser(description="골프장 티타임 검색 대시보드")
    ap.add_argument("--host", default="127.0.0.1",
                    help="바인딩 주소 (기본: 내 PC에서만 접속 가능)")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--courses", help="골프장 CSV 경로")
    ap.add_argument("--sources", help="소스 설정 JSON 경로")
    ap.add_argument("--teetimes", help="티타임 CSV를 소스로 추가")
    ap.add_argument("--routing", default="",
                    help="길찾기 제공자 순서. 예: kakao,osrm,estimate")
    args = ap.parse_args()

    providers = [p.strip() for p in args.routing.split(",") if p.strip()] or None
    try:
        state = make_state(
            courses_path=args.courses,
            sources_path=args.sources,
            csv_teetimes=args.teetimes,
            routing_providers=providers,
        )
    except ValueError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    serve(state, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
