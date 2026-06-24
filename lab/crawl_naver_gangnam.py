# -*- coding: utf-8 -*-
"""
lab: 강남구 한정, 네이버부동산 6개 타입(아파트/오피스텔/빌라/원룸/단독다가구/상가) 전수 수집.

목록 스캔(타입별 페이지네이션)으로 강남구 전체 매물번호를 모은 뒤, 매물마다 상세 수집
(pipeline/naver/crawl_detail.fetch_row: 상세+단지+학교 API + 좌표 역계산)을 적용해서
naver_listings 스키마(SCHEMA.md) 그대로인 행 하나로 합친다.

- DB(Supabase)는 건드리지 않고 결과를 로컬 JSONL 하나로만 저장(단위테스트용, lab/README.md 참고).
- 목록/상세를 별도 파일로 두지 않는 이유: 운영 테이블(naver_listings)도 하나라서
  로컬 산출물도 그 스키마 하나로 통일 — 여러 파일로 쪼개면 헷갈린다.

사용:
  python lab/crawl_naver_gangnam.py                # 타입당 최대 60페이지(production 기본값)
  python lab/crawl_naver_gangnam.py --max-pages 5   # 빠른 샘플 테스트
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "pipeline", "naver"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import crawl_detail  # noqa: E402
from crawler import NaverLand, build_region_tree, ROOTS, TYPES  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "naver_listings_gangnam.jsonl")


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def list_articles(nl, cortar_no, code, max_pages):
    nos = []
    page = 1
    while page <= max_pages:
        j = nl.articles_page(cortar_no, page, code)
        if not j:
            break
        arts = j.get("articleList", [])
        if not arts:
            break
        nos.extend(a.get("articleNo") for a in arts)
        if not j.get("isMoreData"):
            break
        page += 1
        time.sleep(0.3)
    return nos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=60,
                    help="타입당 페이지 상한(20건/p, production 기본값과 동일)")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    nl = NaverLand(headless=not args.show)
    seen = set()
    by_type_ok = Counter()
    try:
        dongs = build_region_tree(nl, ROOTS, only_sido="서울시", only_gu="강남구")
        print(f"[{now()}] 강남구 동 수: {len(dongs)}")

        jobs = [(cno, sido, sigungu, dong, code)
                for cno, sido, sigungu, dong, lat, lon in dongs for code in TYPES]

        with open(OUT, "w", encoding="utf-8") as f:
            for ji, (cno, sido, sigungu, dong, code) in enumerate(jobs, 1):
                nos = list_articles(nl, cno, code, args.max_pages)
                region = {"sido": sido, "sigungu": sigungu, "dong": dong, "cortarNo": cno}
                ok = 0
                for no in nos:
                    if no in seen:
                        continue
                    seen.add(no)
                    try:
                        row = crawl_detail.fetch_row(nl, no, region)
                    except Exception as e:
                        print(f"[{now()}] {code} {no} 예외: {repr(e)[:70]}")
                        row = None
                    if row:
                        row["crawled_at"] = now()
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
                        f.flush()
                        ok += 1
                        by_type_ok[code] += 1
                    time.sleep(0.3)
                print(f"[{now()}] ({ji}/{len(jobs)}) {dong} {TYPES[code]}: "
                      f"목록 {len(nos)}건 -> 상세 {ok}건 적재 (전체 누적 {len(seen)})")
                if ji % 20 == 0:
                    nl.restart()
    finally:
        nl.close()

    print(f"\n[{now()}] 완료. 타입별 적재 건수:")
    for code in TYPES:
        print(f"  {TYPES[code]}: {by_type_ok[code]}")
    print(f"총 {sum(by_type_ok.values())}건 -> {OUT}")


if __name__ == "__main__":
    main()
