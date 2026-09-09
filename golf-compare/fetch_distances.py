#!/usr/bin/env python3
"""
강남역 기준, 골프장 목록의 실제 자동차 이동시간/거리를 카카오모빌리티 API로 계산합니다.

이 스크립트는 반드시 "로컬(사용자 PC)"에서 실행하세요. Claude Code 원격 세션은
네트워크 정책상 dapi.kakao.com / apis-navi.kakaomobility.com 에 접근할 수 없습니다.

사용법:
    export KAKAO_REST_API_KEY=발급받은키
    python3 fetch_distances.py                    # courses_seed.json 사용, 90분 이내만 출력
    python3 fetch_distances.py --max-minutes 120   # 기준 시간 변경
    python3 fetch_distances.py --input my_list.json --output result.json

출력된 JSON을 golf-compare 웹앱의 "이동시간 실측 반영" 패널에 붙여넣으면
지역 평균 추정치가 실측값으로 갱신됩니다.

필요 조건:
  - 카카오 디벨로퍼스(https://developers.kakao.com/console/app) REST API 키
  - 해당 앱에 "카카오모빌리티" 상품(길찾기 API) 신청/활성화
  - 파이썬 3.7+ (표준 라이브러리만 사용, 별도 설치 불필요)
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

KAKAO_LOCAL_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/v1/directions"
ORIGIN_KEYWORD = "강남역"


def kakao_get(url, api_key, params):
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + query, headers={"Authorization": "KakaoAK " + api_key})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} for {url}: {body}") from e


def geocode_keyword(api_key, keyword):
    data = kakao_get(KAKAO_LOCAL_KEYWORD_URL, api_key, {"query": keyword, "size": 1})
    docs = data.get("documents") or []
    if not docs:
        return None
    d = docs[0]
    return {
        "name": d.get("place_name"),
        "address": d.get("road_address_name") or d.get("address_name"),
        "lng": float(d["x"]),
        "lat": float(d["y"]),
    }


def get_driving_duration(api_key, origin, dest):
    params = {
        "origin": f"{origin['lng']},{origin['lat']}",
        "destination": f"{dest['lng']},{dest['lat']}",
        "priority": "RECOMMEND",
    }
    data = kakao_get(KAKAO_DIRECTIONS_URL, api_key, params)
    routes = data.get("routes") or []
    if not routes or routes[0].get("result_code") != 0:
        return None
    summary = routes[0]["summary"]
    return {
        "duration_sec": summary["duration"],
        "distance_m": summary["distance"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default=os.path.join(os.path.dirname(__file__), "courses_seed.json"))
    parser.add_argument("--output", default="distances_output.json")
    parser.add_argument("--max-minutes", type=float, default=90)
    parser.add_argument("--origin", default=ORIGIN_KEYWORD, help="기준 출발지 (기본값: 강남역)")
    parser.add_argument("--sleep", type=float, default=0.3, help="API 호출 간 대기 시간(초)")
    args = parser.parse_args()

    api_key = os.environ.get("KAKAO_REST_API_KEY")
    if not api_key:
        print("환경변수 KAKAO_REST_API_KEY 가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    with open(args.input, encoding="utf-8") as f:
        courses = json.load(f)

    print(f"[1/3] 출발지 '{args.origin}' 좌표 조회 중...")
    origin = geocode_keyword(api_key, args.origin)
    if not origin:
        print(f"출발지 '{args.origin}'를 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)
    print(f"      -> {origin['address']} ({origin['lng']}, {origin['lat']})")

    print(f"[2/3] {len(courses)}개 골프장 위치/이동시간 조회 중...")
    results = []
    for i, course in enumerate(courses, 1):
        name = course["name"]
        try:
            place = geocode_keyword(api_key, name)
            if not place:
                print(f"  ({i}/{len(courses)}) {name}: 검색 결과 없음, 건너뜀")
                continue
            time.sleep(args.sleep)
            route = get_driving_duration(api_key, origin, place)
            if not route:
                print(f"  ({i}/{len(courses)}) {name}: 경로를 찾을 수 없음, 건너뜀")
                continue
            minutes = round(route["duration_sec"] / 60)
            km = round(route["distance_m"] / 1000, 1)
            print(f"  ({i}/{len(courses)}) {name}: {minutes}분 / {km}km")
            results.append({
                "name": name,
                "address": place["address"],
                "lng": place["lng"],
                "lat": place["lat"],
                "driveMinutes": minutes,
                "distanceKm": km,
            })
        except RuntimeError as e:
            print(f"  ({i}/{len(courses)}) {name}: 오류 - {e}")
        time.sleep(args.sleep)

    print(f"[3/3] {args.max_minutes}분 이내로 필터링 및 정렬...")
    within_range = [r for r in results if r["driveMinutes"] <= args.max_minutes]
    within_range.sort(key=lambda r: r["driveMinutes"])

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(within_range, f, ensure_ascii=False, indent=2)

    print(f"\n완료: {len(within_range)}/{len(results)}곳이 {args.max_minutes}분 이내. 결과 저장: {args.output}")
    print("이 파일 내용을 골프장 가격 비교 웹앱의 '이동시간 실측 반영' 패널에 붙여넣으세요.")


if __name__ == "__main__":
    main()
