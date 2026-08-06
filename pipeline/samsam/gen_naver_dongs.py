# -*- coding: utf-8 -*-
"""최신 samsam_snapshots 예약률로 네이버 크롤 대상 동 목록을 재생성한다.

네이버 부동산 로컬 크롤(deploy/crawl_naver_local.py)은 "예약률 낮은 곳 제외"가 정책이라
대상 동을 예약률 30%+ 동으로 한정한다. 이 목록은 삼삼 예약률이 매일 바뀌므로
고정 파일이면 낡는다 → 삼삼 일일 크롤(snapshot.py 직후) 끝에 이 스크립트로 재생성한다.

파일이 둘인 이유: 네이버 크롤은 시도별로 나눠 실행하는데(수도권 한 번 + 추가 지역별 한 번),
동 이름만 담긴 화이트리스트라 한 파일을 공유하면 동명이인(예: 여러 시도의 '중앙동')이
대상 밖 지역까지 끌고 온다. 그래서 수도권용/추가 지역(부산·천안)용을 따로 쓴다.
  deploy/naver_target_dongs.txt        — 수도권
  deploy/naver_target_dongs_extra.txt  — common/target_regions.py의 EXTRA_REGIONS

동 단위 예약률 = 그 동의 (건물유형별 avg_occ_1m)를 매물수(n) 가중 평균.
안전장치: 수도권 목록이 비정상적으로 적으면(기존의 50% 미만) 파일을 건드리지 않는다
(부분 스냅샷에 목록이 쪼그라드는 사고 방지).
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "common"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import db  # noqa: E402
import target_regions  # noqa: E402

OUT = os.path.join(BASE, "deploy", "naver_target_dongs.txt")
OUT_EXTRA = os.path.join(BASE, "deploy", "naver_target_dongs_extra.txt")
THRESHOLD = float(os.environ.get("NAVER_DONG_OCC_MIN", "30"))  # avg_occ_1m 0~100


def _weighted(rows):
    """[(dong, n, occ)] → 예약률 THRESHOLD 이상인 동 이름(정렬)."""
    agg = {}  # dong → [가중합, 매물수합]
    for dong, n, occ in rows:
        n = n or 0
        if occ is None or n <= 0:
            continue
        s = agg.setdefault(dong, [0.0, 0])
        s[0] += occ * n
        s[1] += n
    return sorted(dg for dg, (w, tot) in agg.items() if tot and (w / tot) >= THRESHOLD)


def compute(conn):
    """(스냅샷일, 수도권 동, 추가 지역 동)."""
    d = conn.execute("SELECT MAX(snapshot_date) FROM samsam_snapshots").fetchone()[0]
    if not d:
        return d, [], []
    rows = conn.execute(
        "SELECT sido, sigungu, dong, n, avg_occ_1m FROM samsam_snapshots "
        "WHERE snapshot_date=%s AND dong IS NOT NULL AND dong<>''", (d,)).fetchall()
    metro, extra = [], []
    for sido, sigungu, dong, n, occ in rows:
        if (sido or "").startswith(target_regions.METRO_PREFIX):
            metro.append((dong, n, occ))
        elif target_regions.in_target(sido, sigungu):
            extra.append((dong, n, occ))
    return d, _weighted(metro), _weighted(extra)


def _write(path, dongs):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(dongs) + "\n")


def _count(path):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for ln in f if ln.strip())


def main():
    conn = db.connect()
    d, dongs, extra = compute(conn)
    conn.close()
    if not dongs:
        print(f"[skip] 스냅샷({d}) 예약률 데이터 없음 — 파일 유지", flush=True)
        return
    prev = _count(OUT)
    if prev and len(dongs) < prev * 0.5:
        print(f"[skip] 재계산 {len(dongs)}개 < 기존 {prev}개의 50% — 이상치, 파일 유지", flush=True)
    else:
        _write(OUT, dongs)
        print(f"[done] 예약률 {THRESHOLD:g}%+ 수도권 동 {len(dongs)}개 재생성 "
              f"(스냅샷 {d}, 기존 {prev}개) → {OUT}", flush=True)

    if target_regions.EXTRA_REGIONS:
        prev_e = _count(OUT_EXTRA)
        if extra:
            _write(OUT_EXTRA, extra)
            print(f"[done] 추가 지역({', '.join(target_regions.EXTRA_REGIONS)}) 동 "
                  f"{len(extra)}개 (기존 {prev_e}개) → {OUT_EXTRA}", flush=True)
        else:
            print(f"[skip] 추가 지역 예약률 {THRESHOLD:g}%+ 동 0개 — 파일 유지"
                  f"(기존 {prev_e}개)", flush=True)


if __name__ == "__main__":
    main()
