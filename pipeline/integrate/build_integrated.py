# -*- coding: utf-8 -*-
"""
통합 수익성 분석: samsam_listings × naver_listings (Supabase).
출력: data/net_profit_integrated.csv

핵심 비교: "삼삼엠투 단기임대로 돌렸을 때 실현수익" vs "같은 집을 네이버 장기월세로 줬을 때 비용".

보증금 정규화(전월세 전환):
  네이버 같은 평형이라도 보증금/월세 조합이 제각각(보증금 1억·월세 30 ↔ 보증금 1천·월세 80)이라,
  보증금 큰 매물을 그대로 쓰면 월세가 낮아 단기임대 순수익이 과대평가된다.
  → 모든 네이버 매물을 "환산월세"로 통일해서 비교한다:
        환산월세 = 월세 + 보증금(만원) × 전월세전환율 / 12
  전환율 연 6%(CONV_RATE) 가정. 보증금이 얼마든 동일한 월 비용 기준이 되어 공정해진다.
  (추가로 --max-deposit 으로 특정 보증금 이하 매물만 쓰도록 하드 필터도 걸 수 있음.)

같은 오피스텔 삼삼 매물 수:
  "이 건물(오피스텔)에 삼삼 단기임대가 몇 개나 올라와 있는지"(삼삼동일건물매물수). 건물명+동 기준,
  건물명 없으면 좌표(약 11m) 기준으로 묶어 카운트(자기 포함).
"""
import argparse, csv, json, math, os, re, statistics, sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
import db

OUT = os.path.join(BASE, 'data', 'net_profit_integrated.csv')

WEEKS = 4.345        # 월 → 주 환산
AREA_PCT = 0.15      # 면적 허용 오차 ±15%
CONV_RATE = 0.06     # 전월세 전환율(연). 환산월세 = 월세 + 보증금(만원) × CONV_RATE / 12


# ── 공통 유틸 ──────────────────────────────────────────────────────────────────
def norm(s):
    """건물명 정규화: 괄호 제거 + 특수문자 제거."""
    if not s:
        return ''
    s = re.sub(r'\(.*?\)', '', s)
    return re.sub(r'[^가-힣A-Za-z0-9]', '', s)


def dist(a, b, x, y):
    return math.hypot((a - x) * 111000, (b - y) * 88800)


def room_label(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return '기타'
    return {1: '원룸', 2: '투룸'}.get(n, '쓰리룸+')


def rent_equiv(h):
    """네이버 매물의 보증금 정규화 환산월세(만원). 월세 + 보증금 × 전환율/12."""
    return (h['rent_monthly'] or 0) + (h['deposit'] or 0) * CONV_RATE / 12


def bldg_key(s):
    """삼삼 매물의 '같은 건물' 그룹 키. 건물명 있으면 동+건물명, 없으면 좌표(~11m)."""
    nb = norm(s['building_name'])
    if nb and len(nb) >= 3:
        return ('B', s['sido'], s['sigungu'], s['dong'], nb)
    if s.get('lat') and s.get('lng'):
        return ('G', round(float(s['lat']), 4), round(float(s['lng']), 4))
    return ('X', s['room_id'])   # 식별 불가 → 단독(카운트 1)


# ── 데이터 로드 ────────────────────────────────────────────────────────────────
def load_sam():
    conn = db.connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT room_id, url, name, building_type, building_name, floor,"
        " lat, lng, area_m2, area_pyeong, rooms, sido, sigungu, dong,"
        " rent_weekly, maintenance_weekly, rent_total_weekly,"
        " booked_days_1m, blocked_days_1m, station_500m_names"
        " FROM samsam_listings"
        " WHERE lat IS NOT NULL AND rent_total_weekly > 0"
    ).fetchall()]
    conn.close()
    return rows


def load_nav():
    conn = db.connect()
    # 삼삼은 오피스텔/원룸 단기임대 중심 → 네이버도 오피스텔(OPST)만 사용해
    # 아파트/상가 등 엉뚱한 타입과의 오매칭을 막는다. (building_type_code 컬럼 없던 구 데이터는
    # NULL 이므로 'OPST' 필터에서 제외됨 — 재크롤 후 채워짐.)
    rows = [dict(r) for r in conn.execute(
        "SELECT article_no, url, building_name, area_exclusive_m2,"
        " rent_monthly, deposit, maintenance_monthly, floor_current,"
        " lat, lng, dong"
        " FROM naver_listings"
        " WHERE rent_monthly BETWEEN 5 AND 2000 AND lat IS NOT NULL"
        " AND building_type_code = 'OPST'"
    ).fetchall()]
    conn.close()
    return rows


# ── 매칭 로직 ──────────────────────────────────────────────────────────────────
def _floor_ok(nav_floor, sam_floor):
    if not sam_floor or nav_floor is None:
        return True
    return abs(nav_floor - sam_floor) <= 3


def strict_match(s, nidx, fine):
    """(매칭 매물 리스트, 건물 전체 매물 리스트) 반환.

    건물명이 있으면 동 내 이름 매칭 우선.
    없으면 GPS 15m 이내만 허용 (인접 건물 오매칭 방지).
    """
    slat, slng = float(s['lat']), float(s['lng'])
    s_m2 = float(s['area_m2'] or (s['area_pyeong'] or 0) * 3.305785)
    sb = norm(s['building_name'])
    sam_floor = s['floor']
    cands = {}

    if sb and len(sb) >= 3:
        for nv in nidx.get(s['dong'], []):
            if nv['_n'] == sb or (len(sb) >= 4 and (sb in nv['_n'] or nv['_n'] in sb)):
                if dist(slat, slng, nv['lat'], nv['lng']) <= 500:
                    cands[id(nv)] = nv
    else:
        ca, co = round(slat, 3), round(slng, 3)
        for dla in (-0.001, 0, 0.001):
            for dlo in (-0.001, 0, 0.001):
                for nv in fine.get((round(ca + dla, 3), round(co + dlo, 3)), []):
                    if dist(slat, slng, nv['lat'], nv['lng']) <= 15:
                        cands[id(nv)] = nv

    bldg_all = list(cands.values())
    if not s_m2:
        return bldg_all, bldg_all
    area_ok = [nv for nv in bldg_all
               if nv['area_exclusive_m2']
               and abs(nv['area_exclusive_m2'] - s_m2) / s_m2 <= AREA_PCT]
    hits = [nv for nv in area_ok if _floor_ok(nv.get('floor_current'), sam_floor)]
    return hits, bldg_all


def build_rows(sam, nav, max_deposit=None):
    # 네이버 인덱스: 좌표(소수 3자리) + 동별 건물명
    fine = defaultdict(list)   # (lat3, lng3) → [nv]
    nidx = defaultdict(list)   # dong → [nv]
    for nv in nav:
        nv['_n'] = norm(nv['building_name'])
        fine[(round(nv['lat'], 3), round(nv['lng'], 3))].append(nv)
        if nv['_n']:
            nidx[nv['dong']].append(nv)

    dong_count = Counter(s['dong'] for s in sam)        # 동 단위 삼삼 매물 수
    sam_bldg_count = Counter(bldg_key(s) for s in sam)  # 같은 건물(오피스텔) 삼삼 매물 수

    rows = []
    for s in sam:
        hits, bldg_all = strict_match(s, nidx, fine)
        if not hits:
            continue

        # 보증금 하드 필터(선택). 환산월세가 이미 보증금을 정규화하므로 기본은 미적용.
        use = hits
        if max_deposit is not None:
            capped = [h for h in hits if (h['deposit'] or 0) <= max_deposit]
            use = capped if capped else hits

        rent = statistics.median([h['rent_monthly'] for h in use])       # 원본 월세 중앙값(참고)
        dep = statistics.median([(h['deposit'] or 0) for h in use])      # 보증금 중앙값
        equiv = statistics.median([rent_equiv(h) for h in use])          # 환산월세 중앙값 ★

        mgs = [h['maintenance_monthly'] for h in use
               if h['maintenance_monthly'] not in (None, -1) and h['maintenance_monthly'] > 0]
        navmgmt = (statistics.median(mgs) if mgs
                   else round((s['area_pyeong'] or 0) * 2.0, 1))
        mgmt_known = 1 if mgs else 0

        rep = min(use, key=lambda h: abs(rent_equiv(h) - equiv))
        nav_url = rep.get('url') or f"https://new.land.naver.com/offices?articleNo={rep['article_no']}"
        nav_tot = round(equiv + navmgmt, 1)   # 환산월세 + 관리비 = 장기월세 월 비용(보증금 정규화) ★

        # 삼삼 수익 계산 (원 → 만원)
        sam_week = round(s['rent_total_weekly'] / 10000, 1)
        sam_month = round(sam_week * WEEKS, 1)

        booked = s['booked_days_1m'] or 0
        blocked = s['blocked_days_1m'] or 0
        avail = max(31 - blocked, 1)   # 수집 윈도우 오늘~+30일=31일(양끝 포함) → 예약률 ≤100%
        occ = min(1.0, booked / avail)
        realized = round(sam_week * occ * 30 / 7, 1)

        bldg_rents = [nv['rent_monthly'] for nv in bldg_all if nv['rent_monthly']]
        bldg_cnt = len(bldg_all)

        try:
            station = (json.loads(s['station_500m_names'] or '[]') or [''])[0]
        except Exception:
            station = ''

        rows.append({
            'rid': s['room_id'],
            'name': s['name'],
            'btype': s['building_type'],
            'rooms': room_label(s['rooms']),
            'sido': s['sido'],
            'sigungu': s['sigungu'],
            'dong': s['dong'],
            'station': station,
            'sam_nearby': dong_count[s['dong']] - 1,
            'sam_bldg': sam_bldg_count[bldg_key(s)],   # 같은 오피스텔 삼삼 매물 수(자기 포함)
            'pyeong': s['area_pyeong'] or '',
            'sam_week': sam_week,
            'sam_month': sam_month,
            'bk': booked,
            'bl': blocked,
            'realized': realized,
            'rent': rent,
            'dep': dep,
            'equiv': round(equiv, 1),
            'navmgmt': navmgmt,
            'mgmt_known': mgmt_known,
            'nav_tot': nav_tot,
            'n': len(use),
            'nv_bldg': rep['building_name'],
            'nv_url': nav_url,
            'eff': round(nav_tot / sam_week, 2) if sam_week else 0,
            'real_eff': round(realized / nav_tot, 2) if nav_tot else 0,
            'real_eff_rent': round(realized / equiv, 2) if equiv else 0,
            'net': round(realized - nav_tot, 1),
            'bldg_cnt': bldg_cnt,
            'bldg_rent_min': round(min(bldg_rents), 1) if bldg_rents else '',
            'bldg_rent_med': round(statistics.median(bldg_rents), 1) if bldg_rents else '',
            'bldg_rent_max': round(max(bldg_rents), 1) if bldg_rents else '',
        })

    rows.sort(key=lambda x: x['net'], reverse=True)
    return rows


def write_csv(rows):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow([
            '삼삼ID', '매물명', '건물유형', '방수', '시도', '시군구', '동', '인근역',
            '동삼삼매물수', '삼삼동일건물매물수', '평수', '삼삼주당_만원', '삼삼월환산_만원',
            '1달예약일', '1달막힘일', '1달실현수익_만원',
            '네이버월세_만원', '네이버보증금_만원', '네이버환산월세_만원', '네이버관리비_만원', '관리비표기여부',
            '네이버월총_만원', '매칭매물수',
            '네이버월총÷삼삼주당', '실현효율(1달실현÷네이버월총)',
            '현실효율(1달실현÷네이버환산월세)', '순수익_만원(1달실현−환산월세−관리비)',
            '건물네이버매물수', '건물월세최저_만원', '건물월세중간_만원', '건물월세최고_만원',
            '네이버건물', '네이버링크', '삼삼링크',
        ])
        for r in rows:
            w.writerow([
                r['rid'], r['name'], r['btype'], r['rooms'],
                r['sido'], r['sigungu'], r['dong'], r['station'],
                r['sam_nearby'], r['sam_bldg'], r['pyeong'], r['sam_week'], r['sam_month'],
                r['bk'], r['bl'], r['realized'], r['rent'], r['dep'], r['equiv'], r['navmgmt'],
                '표기' if r['mgmt_known'] else '미표기(평당2만)',
                r['nav_tot'], r['n'], r['eff'],
                r['real_eff'], r['real_eff_rent'], r['net'],
                r['bldg_cnt'], r['bldg_rent_min'], r['bldg_rent_med'], r['bldg_rent_max'],
                r['nv_bldg'], r['nv_url'],
                f"https://web.33m2.co.kr/guest/room/{r['rid']}",
            ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-deposit', type=int, default=None,
                    help='네이버 매칭 시 이 보증금(만원) 이하만 사용(선택). 기본은 환산월세로 정규화하므로 미적용.')
    args = ap.parse_args()

    sam = load_sam()
    nav = load_nav()
    print(f"삼삼: {len(sam)}건 / 네이버(OPST): {len(nav)}건")
    if args.max_deposit is not None:
        print(f"보증금 하드 필터: ≤ {args.max_deposit}만원")

    rows = build_rows(sam, nav, max_deposit=args.max_deposit)
    write_csv(rows)
    print(f"통합 매칭: {len(rows)}건 → {OUT}")
    print("건물유형:", Counter(r['btype'] for r in rows).most_common())
    print("방수:", Counter(r['rooms'] for r in rows).most_common())


if __name__ == '__main__':
    main()
