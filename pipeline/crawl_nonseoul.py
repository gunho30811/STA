# -*- coding: utf-8 -*-
"""
비수도권 네이버 오피스텔 '월세' 크롤 — 삼삼 오피스텔이 있는 시군구만 타겟.
crawler.py 의 브라우저 fetch 엔진(NaverLand)과 crawl_dong 을 재사용.
저장: data/naver_nonseoul.db (db.py 스키마)
"""
import json, os, time, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

sys.path.insert(0, ROOT)
import db
db.DB_PATH = os.path.join(DATA, "naver_nonseoul.db")   # 출력 DB 변경

import crawler
from crawler import NaverLand, crawl_dong, now

SUDO = {"1100000000", "4100000000", "2800000000"}  # 서울/경기/인천 제외

tg = json.load(open(os.path.join(DATA, "targets.json"), encoding="utf-8"))
ROOTS = [(s["nm"], s["no"]) for s in tg["sido"] if s["no"] not in SUDO]
T = set(tg["target_sigungu"])   # 삼삼이 있는 비수도권 시군구 이름 집합


def want_recurse(sig):
    # sig 가 타겟이거나, 타겟의 접두어(예: '포항시' → '포항시 남구')면 더 내려감
    return (sig in T) or any(t.startswith(sig + " ") for t in T)


def collect_dongs(nl):
    dongs = []

    def walk(cortarNo, path):
        for ch in nl.regions(cortarNo):
            name, ctype, cno = ch["cortarName"], ch["cortarType"], ch["cortarNo"]
            newpath = path + [name]
            sig = " ".join(newpath[1:])         # 시/도 제외한 시군구 경로
            if ctype == "sec":                  # 동
                if sig in T or " ".join(path[1:]) in T:
                    sido = path[0]
                    sigungu = " ".join(path[1:])
                    dongs.append((cno, sido, sigungu, name,
                                  ch.get("centerLat"), ch.get("centerLon")))
            else:                               # 시/구 → 타겟 관련일 때만 재귀
                if want_recurse(sig) or want_recurse(" ".join(path[1:])):
                    walk(cno, newpath)
        time.sleep(0.25)

    for sido_name, root_no in ROOTS:
        print(f"[{now()}] 지역 탐색: {sido_name}")
        walk(root_no, [sido_name])
    return dongs


def main():
    db.init_db()
    nl = NaverLand(headless=True)
    try:
        dongs = collect_dongs(nl)
        # regions 테이블 저장
        conn = db.connect()
        conn.executemany(
            "INSERT OR REPLACE INTO regions(cortarNo,sido,sigungu,dong,lat,lon) VALUES(?,?,?,?,?,?)",
            dongs)
        conn.commit(); conn.close()
        print(f"[{now()}] 타겟 동 수: {len(dongs)}")

        grand = 0
        for i, (cno, sido, sigungu, dong, lat, lon) in enumerate(dongs, 1):
            try:
                n = crawl_dong(nl, cno, sido, sigungu, dong)
            except Exception as e:
                print(f"[{now()}] 실패 {sido} {sigungu} {dong}: {repr(e)[:70]}")
                try: nl.restart()
                except Exception: pass
                continue
            grand += n
            print(f"[{now()}] ({i}/{len(dongs)}) {sido} {sigungu} {dong}: {n}건 (누적 {grand})")
            if i % 150 == 0:
                nl.restart()
        print(f"[{now()}] 비수도권 크롤 완료. 총 {grand}건.")
    finally:
        nl.close()


if __name__ == "__main__":
    main()
