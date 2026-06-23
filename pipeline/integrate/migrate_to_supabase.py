# -*- coding: utf-8 -*-
"""
로컬 SQLite → Supabase 1회성 데이터 이전 스크립트.

읽는 파일 (건드리지 않음, 읽기 전용):
  data/naver_opst.db          → regions, crawl_state (수도권)
  data/naver_opst_enriched.db → listings (수도권, mgmt 포함)
  data/naver_nonseoul.db      → regions, crawl_state, listings (비수도권)

쓰는 곳: Supabase (DATABASE_URL)
"""
import os, sys, sqlite3
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
import db

DATA = os.path.join(BASE, "data")
OPST_DB      = os.path.join(DATA, "naver_opst.db")
ENRICHED_DB  = os.path.join(DATA, "naver_opst_enriched.db")
NONSEOUL_DB  = os.path.join(DATA, "naver_nonseoul.db")


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def sqlite_rows(path, sql):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(sql)]
    con.close()
    return rows


def migrate_regions(pg):
    rows = []
    for path, label in [(OPST_DB, "수도권"), (NONSEOUL_DB, "비수도권")]:
        if not os.path.exists(path):
            log(f"{label} DB 없음, 건너뜀: {path}"); continue
        r = sqlite_rows(path, "SELECT cortarNo,sido,sigungu,dong,lat,lon FROM regions")
        rows.extend(r); log(f"regions {label}: {len(r)}건 읽음")

    if not rows: return
    pg.executemany(
        "INSERT OR REPLACE INTO regions(cortarNo,sido,sigungu,dong,lat,lon) VALUES(?,?,?,?,?,?)",
        [(r["cortarNo"], r["sido"], r["sigungu"], r["dong"], r["lat"], r["lon"]) for r in rows]
    )
    pg.commit()
    log(f"regions 총 {len(rows)}건 → Supabase 완료")


def migrate_crawl_state(pg):
    rows = []
    for path, label in [(OPST_DB, "수도권"), (NONSEOUL_DB, "비수도권")]:
        if not os.path.exists(path):
            continue
        r = sqlite_rows(path, "SELECT cortarNo,status,n_articles,updated_at FROM crawl_state")
        rows.extend(r); log(f"crawl_state {label}: {len(r)}건 읽음")

    if not rows: return
    pg.executemany(
        "INSERT OR REPLACE INTO crawl_state(cortarNo,status,n_articles,updated_at) VALUES(?,?,?,?)",
        [(r["cortarNo"], r["status"], r["n_articles"], r["updated_at"]) for r in rows]
    )
    pg.commit()
    log(f"crawl_state 총 {len(rows)}건 → Supabase 완료")


def migrate_listings(pg):
    COLS = [
        "articleNo","sido","sigungu","dong","cortarNo","articleName","buildingName",
        "realEstateType","tradeType","deposit","rent","area_m2","area_real_m2","areaName",
        "floorInfo","direction","confirmYmd","featureDesc","tags","lat","lon",
        "realtorName","cpName","imgUrl","articleUrl","crawled_at","mgmt",
    ]
    col_str = ",".join(COLS)
    ph = ",".join(["?"] * len(COLS))
    sql_upsert = f"INSERT OR REPLACE INTO listings({col_str}) VALUES({ph})"

    def read_listings(path, label):
        if not os.path.exists(path):
            log(f"{label} DB 없음, 건너뜀"); return []
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        existing_cols = {r[1] for r in con.execute("PRAGMA table_info(listings)")}
        sel = ", ".join(c if c in existing_cols else "NULL" for c in COLS)
        rows = [tuple(r) for r in con.execute(f"SELECT {sel} FROM listings")]
        con.close()
        log(f"listings {label}: {len(rows)}건 읽음")
        return rows

    rows = []
    # 수도권은 enriched DB 우선 (mgmt 포함), 없으면 원본
    for path, label in [(ENRICHED_DB, "수도권-enriched"), (OPST_DB, "수도권-원본")]:
        r = read_listings(path, label)
        if r:
            rows.extend(r)
            break  # enriched가 있으면 원본은 건너뜀

    rows.extend(read_listings(NONSEOUL_DB, "비수도권"))

    if not rows:
        log("이전할 listings 없음"); return

    pg.executemany(sql_upsert, rows)
    pg.commit()
    log(f"listings 총 {len(rows)}건 → Supabase 완료")


def main():
    log("Supabase 연결 중...")
    pg = db.connect()
    log("연결 성공. 이전 시작.")

    migrate_regions(pg)
    migrate_crawl_state(pg)
    migrate_listings(pg)

    pg.close()
    log("전체 이전 완료. SQLite 파일은 그대로 유지됩니다.")


if __name__ == "__main__":
    main()
