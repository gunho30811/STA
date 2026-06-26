# -*- coding: utf-8 -*-
"""
오프라인 프리뷰/검증용 합성 삼삼 샘플 생성 → lab/samsam_sample.jsonl

실데이터가 아니라 web/samsam_app.py 를 DB 없이 띄워보기 위한 가짜 데이터다.
옵션 유무에 따라 예약률이 조금씩 달라지도록(신호가 보이도록) 생성한다.
컬럼은 samsam_listings(SCHEMA.md) 중 옵션 공실률 분석에 쓰는 것들.
"""
import json
import os
import random

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samsam_sample.jsonl")

REGIONS = [
    ("서울시", "강남구", ["역삼동", "논현동", "삼성동", "대치동"]),
    ("서울시", "서초구", ["서초동", "반포동", "잠원동"]),
    ("경기도", "성남시 분당구", ["정자동", "서현동"]),
]
BTYPES = ["오피스텔", "오피스텔", "오피스텔", "빌라", "원룸"]  # 오피스텔 비중↑
# 실데이터(삼삼 detail API)와 동일하게 영문 옵션 코드 사용
OPTIONS = ["REFRIGERATOR", "WASHING_MACHINE", "AIR_CONDITIONER", "MICROWAVE", "TV",
           "BED", "DESK", "INDUCTION", "DRYER", "WATER_PURIFIER", "RICE_COOKER"]
# 옵션별 예약률 가중치(있으면 예약률 +): 에어컨·세탁기는 중요, TV는 영향 적게(데모 의도)
WEIGHT = {"AIR_CONDITIONER": 0.12, "WASHING_MACHINE": 0.10, "REFRIGERATOR": 0.08,
          "INDUCTION": 0.05, "DRYER": 0.05, "MICROWAVE": 0.03, "TV": 0.01,
          "BED": 0.04, "DESK": 0.0, "WATER_PURIFIER": 0.02, "RICE_COOKER": 0.0}
STATIONS = {"강남구": "강남역", "서초구": "교대역", "성남시 분당구": "정자역"}


def main():
    rnd = random.Random(20260626)
    rows = []
    rid = 200000
    for _ in range(140):
        sido, sigungu, dongs = rnd.choice(REGIONS)
        dong = rnd.choice(dongs)
        btype = rnd.choice(BTYPES)
        # 옵션 무작위 부분집합(기본옵션 다수 + 추가옵션 일부)
        opts = [o for o in OPTIONS if rnd.random() < 0.55]
        basic = opts[: max(2, len(opts) - 2)]
        extra = opts[len(basic):]
        # 예약률: 기본 + 옵션 가중치 + 잡음, 0.1~0.97 클램프
        base = 0.45 + sum(WEIGHT.get(o, 0) for o in opts) + rnd.uniform(-0.15, 0.15)
        occ = min(0.97, max(0.1, base))
        blocked = rnd.choice([0, 0, 0, 2, 5])
        avail = 30 - blocked
        booked = round(occ * avail)
        pyeong = rnd.choice([6, 7, 8, 9, 10, 12])
        rid += 1
        rows.append({
            "room_id": rid,
            "url": f"https://web.33m2.co.kr/guest/room/{rid}",
            "name": f"{dong} {btype} {pyeong}평 {rnd.choice(['풀옵션','역세권','신축','올수리'])}",
            "building_type": btype,
            "building_name": f"{dong}{rnd.choice(['타워','스카이','리체','팰리스',''])}".strip() or None,
            "sido": sido, "sigungu": sigungu, "dong": dong,
            "area_pyeong": pyeong,
            "rent_total_weekly": rnd.choice([280000, 320000, 350000, 400000, 450000]),
            "booked_days_1m": booked,
            "blocked_days_1m": blocked,
            "booked_days_3m": min(90, round(occ * 90)),
            "basic_options": json.dumps(basic, ensure_ascii=False),
            "extra_options": json.dumps(extra, ensure_ascii=False),
            "station_500m_names": json.dumps([STATIONS.get(sigungu, "")], ensure_ascii=False),
        })
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"합성 샘플 {len(rows)}건 → {OUT}")


if __name__ == "__main__":
    main()
