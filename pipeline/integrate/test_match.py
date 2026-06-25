# -*- coding: utf-8 -*-
"""
매칭 로직 단위 테스트: samsam_listings × naver_listings 결과를 콘솔에 출력.

사용법:
  python pipeline/integrate/test_match.py              # DB 전체 기준
  python pipeline/integrate/test_match.py --sigungu 강남구   # 강남구만
  python pipeline/integrate/test_match.py --limit 20  # 삼삼 20건만
"""
import argparse, json, math, os, re, sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import db

AREA_PCT = 0.15


def norm(s):
    if not s:
        return ''
    s = re.sub(r'\(.*?\)', '', s)
    return re.sub(r'[^가-힣A-Za-z0-9]', '', s)


def dist(a, b, x, y):
    return math.hypot((a - x) * 111000, (b - y) * 88800)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sigungu', default='')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    conn = db.connect()

    # 삼삼 로드
    where_sam = ''
    params_sam = []
    if args.sigungu:
        where_sam = " AND (jibun_address LIKE %s OR road_address LIKE %s)"
        params_sam = [f'%{args.sigungu}%', f'%{args.sigungu}%']
    limit_sql = f' LIMIT {args.limit}' if args.limit else ''

    sam = [dict(r) for r in conn.execute(
        f"SELECT room_id, name, building_type, building_name, floor,"
        f" lat, lng, area_m2, area_pyeong, dong, rent_total_weekly,"
        f" booked_days_1m, road_address, jibun_address"
        f" FROM samsam_listings WHERE lat IS NOT NULL AND rent_total_weekly > 0"
        f"{where_sam}{limit_sql}",
        params_sam or None,
    ).fetchall()]

    # 네이버 로드 (시군구 필터 없으면 도/구 내 dong 기준으로만)
    nav_where = ''
    nav_params = []
    if args.sigungu:
        nav_where = " AND sigungu LIKE %s"
        nav_params = [f'%{args.sigungu}%']

    nav = [dict(r) for r in conn.execute(
        f"SELECT article_no, building_name, area_exclusive_m2, rent_monthly,"
        f" deposit, maintenance_monthly, floor_current, lat, lng, dong, url"
        f" FROM naver_listings WHERE rent_monthly BETWEEN 5 AND 2000 AND lat IS NOT NULL"
        f" AND building_type_code = 'OPST'"
        f"{nav_where}",
        nav_params or None,
    ).fetchall()]
    conn.close()

    print(f"\n삼삼: {len(sam)}건  |  네이버: {len(nav)}건\n{'='*70}")

    if not sam:
        print("samsam_listings 데이터 없음. 크롤러 먼저 실행:")
        print(f"  python pipeline/samsam/crawler.py --limit 20 --sigungu {args.sigungu or '강남구'}")
        return
    if not nav:
        print("naver_listings 데이터 없음. 네이버 크롤러 먼저 실행")
        return

    # 인덱스
    fine = defaultdict(list)
    nidx = defaultdict(list)
    for nv in nav:
        nv['_n'] = norm(nv['building_name'])
        fine[(round(nv['lat'], 3), round(nv['lng'], 3))].append(nv)
        if nv['_n']:
            nidx[nv['dong']].append(nv)

    matched = unmatched = 0
    for s in sam:
        slat, slng = float(s['lat']), float(s['lng'])
        s_m2 = float(s['area_m2'] or (s['area_pyeong'] or 0) * 3.305785)
        sb = norm(s['building_name'])
        cands = {}

        if sb and len(sb) >= 3:
            for nv in nidx.get(s['dong'], []):
                if nv['_n'] == sb or (len(sb) >= 4 and (sb in nv['_n'] or nv['_n'] in sb)):
                    d = dist(slat, slng, nv['lat'], nv['lng'])
                    if d <= 500:
                        cands[id(nv)] = (nv, d)
        else:
            ca, co = round(slat, 3), round(slng, 3)
            for dla in (-0.001, 0, 0.001):
                for dlo in (-0.001, 0, 0.001):
                    for nv in fine.get((round(ca + dla, 3), round(co + dlo, 3)), []):
                        d = dist(slat, slng, nv['lat'], nv['lng'])
                        if d <= 15:
                            cands[id(nv)] = (nv, d)

        bldg_all = [(nv, d) for nv, d in cands.values()]
        area_ok = [(nv, d) for nv, d in bldg_all
                   if s_m2 and nv['area_exclusive_m2']
                   and abs(nv['area_exclusive_m2'] - s_m2) / s_m2 <= AREA_PCT]
        hits = [(nv, d) for nv, d in area_ok
                if not s['floor'] or nv['floor_current'] is None
                or abs(nv['floor_current'] - s['floor']) <= 3]

        addr_short = (s['jibun_address'] or s['road_address'] or '')[:40]
        print(f"\n[삼삼 {s['room_id']}] {s['name'][:30]}")
        print(f"  주소: {addr_short}  /  {s['building_type']} {s['area_pyeong']}평"
              f"  {s['floor']}층  주당{s['rent_total_weekly']//10000}만원")
        print(f"  건물명(정규화): '{s['building_name']}' → '{sb}'  dong='{s['dong']}'")

        if not hits:
            print(f"  ✗ 매칭 없음  (건물후보:{len(bldg_all)} 면적OK:{len(area_ok)})")
            unmatched += 1
        else:
            matched += 1
            for nv, d in sorted(hits, key=lambda x: x[1])[:3]:
                area_diff = abs((nv['area_exclusive_m2'] or 0) - s_m2)
                floor_diff = (abs(nv['floor_current'] - s['floor'])
                              if s['floor'] and nv['floor_current'] else '?')
                print(f"  ✓ [{nv['article_no']}] {nv['building_name']}"
                      f"  {nv['area_exclusive_m2']}㎡(Δ{area_diff:.1f})"
                      f"  {nv['floor_current']}층(Δ{floor_diff})"
                      f"  월세{nv['rent_monthly']}만  거리{d:.0f}m")

    print(f"\n{'='*70}")
    print(f"결과: 매칭 {matched}건 / 미매칭 {unmatched}건 / 전체 {len(sam)}건")
    rate = matched / len(sam) * 100 if sam else 0
    print(f"매칭률: {rate:.1f}%")


if __name__ == '__main__':
    main()
