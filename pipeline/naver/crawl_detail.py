# -*- coding: utf-8 -*-
"""
네이버 상세 수집기: listings(목록)의 각 매물을 상세 API 3종 + 좌표 역계산으로
보강해 naver_listings 테이블에 적재한다.

매물당 호출:
  1) /api/articles/{no}            가격·면적·시설·현관구조·중개사·summary
  2) /api/complexes/{hscpNo}       도로명·지번·용적률·건폐율·세대·주차·건설사
  3) /api/complexes/{hscpNo}/schools  배정 초등학교
  + subway.py 로 좌표 기반 최근접 지하철역 계산

이미 naver_listings 에 있는 매물은 건너뛴다(이어받기). crawler.NaverLand 재사용.

사용:
  python pipeline/naver/crawl_detail.py                 # listings 전체
  python pipeline/naver/crawl_detail.py --sido 서울시    # 특정 시/도
  python pipeline/naver/crawl_detail.py --limit 20      # N건만(테스트)
  python pipeline/naver/crawl_detail.py --redo          # 이미 받은 것도 다시
"""
import argparse
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import db
import crawler
from crawler import NaverLand
import detail_map

API = "https://new.land.naver.com/api"


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fetch_row(nl, no, region):
    """매물 1건 → naver_listings 행 dict (실패 시 None)."""
    detail = nl.api(f"{API}/articles/{no}?complexNo=")
    if not detail or "articleDetail" not in detail:
        return None
    hscp = (detail.get("articleDetail") or {}).get("hscpNo")
    complex_detail, schools = {}, []
    if hscp:
        cj = nl.api(f"{API}/complexes/{hscp}")
        complex_detail = (cj or {}).get("complexDetail", {}) or {}
        sj = nl.api(f"{API}/complexes/{hscp}/schools")
        schools = (sj or {}).get("schools", []) or []
    return detail_map.map_row(detail, complex_detail, schools, region=region)


def save(rows):
    if not rows:
        return
    cols = detail_map.COLUMNS + ["crawled_at"]
    ph = ",".join("?" * len(cols))
    sql = f"INSERT OR REPLACE INTO naver_listings({','.join(cols)}) VALUES({ph})"
    conn = db.connect()
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    conn.commit()
    conn.close()


def source_articles(args):
    """listings 에서 (articleNo, region) 목록. 이미 받은 건 제외(--redo 면 포함)."""
    conn = db.connect()
    done = set()
    if not args.redo:
        done = {r[0] for r in conn.execute("SELECT article_no FROM naver_listings")}
    q = ("SELECT articleNo, sido, sigungu, dong, cortarNo FROM listings "
         "WHERE articleNo IS NOT NULL")
    p = []
    if args.sido:
        q += " AND sido LIKE ?"; p.append(f"%{args.sido}%")
    if args.gu:
        q += " AND sigungu LIKE ?"; p.append(f"%{args.gu}%")
    ex = [t.strip() for t in (args.exclude_types or "").split(",") if t.strip()]
    if ex:
        q += f" AND (realEstateType IS NULL OR realEstateType NOT IN ({','.join('?' * len(ex))}))"
        p += ex
    out = []
    for r in conn.execute(q, p):
        no = r[0]
        if int(no) in done:
            continue
        out.append((no, {"sido": r[1], "sigungu": r[2], "dong": r[3], "cortarNo": r[4]}))
    conn.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sido")
    ap.add_argument("--gu")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.4,
                    help="매물당 대기(초). 차단 방지로 전국 대량 수집 시 0.8~1.2 권장")
    args = ap.parse_args()

    db.init_db()
    todo = source_articles(args)
    if args.limit:
        todo = todo[:args.limit]
    print(f"[{now()}] 상세 수집 대상: {len(todo)}건")
    if not todo:
        return

    nl = NaverLand(headless=not args.show)
    buf, done = [], 0
    try:
        for i, (no, region) in enumerate(todo, 1):
            try:
                row = fetch_row(nl, no, region)
            except Exception as e:
                print(f"[{now()}] {no} 실패: {repr(e)[:70]}")
                try:
                    nl.restart()
                except Exception:
                    pass
                continue
            if row:
                row["crawled_at"] = now()
                buf.append(row)
                done += 1
            if len(buf) >= 50:
                save(buf); buf = []
                print(f"[{now()}] ({i}/{len(todo)}) 누적 {done}건 저장")
            time.sleep(args.sleep)
            if i % 150 == 0:
                nl.restart()
        save(buf)
        print(f"[{now()}] 완료. 총 {done}건 적재.")
    finally:
        nl.close()


if __name__ == "__main__":
    main()
