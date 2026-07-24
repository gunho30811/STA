# -*- coding: utf-8 -*-
"""최신 samsam_snapshots 예약률로 네이버 크롤 대상 동 목록을 재생성한다.

네이버 부동산 로컬 크롤(deploy/crawl_naver_local.py)은 "예약률 낮은 곳 제외"가 정책이라
대상 동을 예약률 30%+ 수도권 동으로 한정한다. 이 목록은 삼삼 예약률이 매일 바뀌므로
고정 파일이면 낡는다 → 삼삼 일일 크롤(snapshot.py 직후) 끝에 이 스크립트로 재생성한다.

동 단위 예약률 = 그 동의 (건물유형별 avg_occ_1m)를 매물수(n) 가중 평균.
결과를 deploy/naver_target_dongs.txt에 덮어쓴다. 안전장치: 조회 결과가 비정상적으로
적으면(기존의 50% 미만) 파일을 건드리지 않는다(부분 스냅샷에 목록이 쪼그라드는 사고 방지).
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import db  # noqa: E402

OUT = os.path.join(BASE, "deploy", "naver_target_dongs.txt")
METRO = ("서울시", "경기도", "인천시")
THRESHOLD = float(os.environ.get("NAVER_DONG_OCC_MIN", "30"))  # avg_occ_1m 0~100


def compute(conn):
    d = conn.execute("SELECT MAX(snapshot_date) FROM samsam_snapshots").fetchone()[0]
    if not d:
        return d, []
    ph = ", ".join(["%s"] * len(METRO))
    rows = conn.execute(
        "SELECT dong, n, avg_occ_1m FROM samsam_snapshots "
        f"WHERE snapshot_date=%s AND sido IN ({ph}) AND dong IS NOT NULL AND dong<>''",
        (d, *METRO)).fetchall()
    agg = {}  # dong → [가중합, 매물수합]
    for dong, n, occ in rows:
        n = n or 0
        if occ is None or n <= 0:
            continue
        s = agg.setdefault(dong, [0.0, 0])
        s[0] += occ * n
        s[1] += n
    dongs = sorted(dg for dg, (w, tot) in agg.items() if tot and (w / tot) >= THRESHOLD)
    return d, dongs


def main():
    conn = db.connect()
    d, dongs = compute(conn)
    conn.close()
    if not dongs:
        print(f"[skip] 스냅샷({d}) 예약률 데이터 없음 — 파일 유지", flush=True)
        return
    prev = 0
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            prev = sum(1 for ln in f if ln.strip())
    if prev and len(dongs) < prev * 0.5:
        print(f"[skip] 재계산 {len(dongs)}개 < 기존 {prev}개의 50% — 이상치, 파일 유지", flush=True)
        return
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(dongs) + "\n")
    print(f"[done] 예약률 {THRESHOLD:g}%+ 수도권 동 {len(dongs)}개 재생성 "
          f"(스냅샷 {d}, 기존 {prev}개) → {OUT}", flush=True)


if __name__ == "__main__":
    main()
