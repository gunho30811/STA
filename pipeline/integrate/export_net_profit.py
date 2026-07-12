# -*- coding: utf-8 -*-
"""
data/net_profit_integrated.csv → net_profit 테이블(Supabase) 적재.

크롤/웹 분리(계약=DB)로, build_integrated.py 가 만든 CSV를 DB에 upsert 한다.
웹(profit_app/samsam_app)은 이 테이블만 읽는다. build_integrated 실행 뒤 이 스크립트를 돌리면 됨.
(export_jsonl.py 와 동일한 '가공 결과 → DB 적재' 패턴.)

    python pipeline/integrate/export_net_profit.py
"""
import csv
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import db  # noqa: E402

CSV = os.path.join(BASE, "data", "net_profit_integrated.csv")

# CSV 한글 헤더 → net_profit 테이블 컬럼(웹 내부 짧은키와 동일).
COL_MAP = {
    "삼삼ID": "id", "매물명": "name", "건물유형": "btype", "방수": "rooms",
    "시도": "sido", "시군구": "sigungu", "동": "dong", "인근역": "station",
    "동삼삼매물수": "dongCnt", "삼삼동일건물매물수": "samBldg", "평수": "pyeong",
    "삼삼주당_만원": "wk", "삼삼월환산_만원": "maxRev", "1달실현수익_만원": "realRev",
    "1달예약일": "bk", "1달막힘일": "bl",
    "네이버월세_만원": "nRent", "네이버보증금_만원": "nDep", "네이버환산월세_만원": "nEquiv",
    "네이버관리비_만원": "nMgmt", "관리비표기여부": "mgmtFlag", "네이버월총_만원": "nTotal",
    "매칭매물수": "matches", "네이버월총÷삼삼주당": "mult",
    "건물네이버매물수": "bldgCnt", "건물월세최저_만원": "bldgRentMin",
    "건물월세중간_만원": "bldgRentMed", "건물월세최고_만원": "bldgRentMax",
    "네이버건물": "bldg", "네이버링크": "naverUrl", "삼삼링크": "samUrl",
    "월별예약JSON": "monthOcc",
    "부동산번호": "phone", "중개사무소": "office",
    "대표월세_만원": "repRent", "대표보증금_만원": "repDep", "대표층수": "repFloor",
}
COLS = list(dict.fromkeys(COL_MAP.values()))   # 테이블 컬럼 순서(중복 제거)
NUM = {"dongCnt", "samBldg", "pyeong", "wk", "maxRev", "realRev", "bk", "bl",
       "nRent", "nDep", "nEquiv", "nMgmt", "nTotal", "matches", "mult",
       "bldgCnt", "bldgRentMin", "bldgRentMed", "bldgRentMax", "repRent", "repDep"}


def _num(v):
    s = (v or "").replace(",", "").strip()
    if s in ("", "-") or s.startswith("미표기"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _val(key, raw):
    if key in NUM:
        return _num(raw)
    return (raw or "").strip() or None


def main():
    if not os.path.exists(CSV):
        print(f"CSV 없음: {CSV}")
        return
    rows = []
    with open(CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rec = {c: None for c in COLS}
            for kr, key in COL_MAP.items():
                if kr in r:
                    rec[key] = _val(key, r[kr])
            if rec.get("id") is None:
                continue
            rec["id"] = int(rec["id"])
            rows.append(tuple(rec[c] for c in COLS))

    placeholders = ", ".join(["%s"] * len(COLS))
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in COLS if c != "id")
    sql = (f"INSERT INTO net_profit ({', '.join(COLS)}) VALUES ({placeholders}) "
           f"ON CONFLICT (id) DO UPDATE SET {updates}")

    conn = db.connect()
    # 전량 재적재: 없어진 매물이 남지 않게 비우고 다시 넣는다(CSV가 전체 스냅샷이므로).
    conn.execute("DELETE FROM net_profit")
    conn.executemany(sql, rows)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM net_profit").fetchone()[0]
    conn.close()
    print(f"net_profit 적재 완료: {len(rows)}건 → DB {n}건")


if __name__ == "__main__":
    main()
