# -*- coding: utf-8 -*-
"""
통합 수익성 분석: samsam_listings × naver_listings (Supabase).
출력: data/net_profit_integrated.csv
"""
import csv, math, os, re, statistics, sys
from collections import Counter, defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
import db

OUT = os.path.join(BASE, 'data', 'net_profit_integrated.csv')

WEEKS = 4.345        # 월 → 주 환산
AREA_PCT = 0.15      # 면적 허용 오차 ±15%
PURE_DEP_MAN = 2000  # 순수 월세 기준 보증금 상한(만원)


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
    rows = [dict(r) for r in conn.execute(
        "SELECT article_no, url, building_name, area_exclusive_m2,"
        " rent_monthly, deposit, maintenance_monthly, floor_current,"
        " lat, lng, dong"
        " FROM naver_listings"
        " WHERE rent_monthly BETWEEN 5 AND 2000 AND lat IS NOT NULL"
    ).fetchall()]
    conn.close()
    return rows


# ── 인덱스 ─────────────────────────────────────────────────────────────────────
sam = load_sam()
nav = load_nav()
print(f"삼삼: {len(sam)}건 / 네이버: {len(nav)}건")

# 네이버 인덱스: 좌표(소수 3자리) + 동별 건물명
fine = defaultdict(list)   # (lat3, lng3) → [nv]
nidx = defaultdict(list)   # dong → [nv]
for nv in nav:
    nv['_n'] = norm(nv['building_name'])
    fine[(round(nv['lat'], 3), round(nv['lng'], 3))].append(nv)
    if nv['_n']:
        nidx[nv['dong']].append(nv)

# 동별 삼삼 매물 수(자기 제외용)
dong_count = Counter(s['dong'] for s in sam)


# ── 매칭 로직 ──────────────────────────────────────────────────────────────────
def _floor_ok(nav_floor, sam_floor):
    if not sam_floor or nav_floor is None:
        return True
    return abs(nav_floor - sam_floor) <= 3


def strict_match(s):
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


# ── 통합 계산 ──────────────────────────────────────────────────────────────────
rows = []
for s in sam:
    hits, bldg_all = strict_match(s)
    if not hits:
        continue

    pure = [h for h in hits if (h['deposit'] or 0) <= PURE_DEP_MAN]
    use = pure if pure else sorted(hits, key=lambda h: (h['deposit'] or 0))[:1]

    rent = statistics.median([h['rent_monthly'] for h in use])
    dep = statistics.median([(h['deposit'] or 0) for h in use])

    mgs = [h['maintenance_monthly'] for h in hits
           if h['maintenance_monthly'] not in (None, -1) and h['maintenance_monthly'] > 0]
    navmgmt = (statistics.median(mgs) if mgs
               else round((s['area_pyeong'] or 0) * 2.0, 1))
    mgmt_known = 1 if mgs else 0

    rep = min(use, key=lambda h: abs(h['rent_monthly'] - rent))
    nav_url = rep.get('url') or f"https://new.land.naver.com/offices?articleNo={rep['article_no']}"
    nav_tot = rent + navmgmt

    # 삼삼 수익 계산 (원 → 만원)
    sam_week = round(s['rent_total_weekly'] / 10000, 1)
    sam_month = round(sam_week * WEEKS, 1)

    booked = s['booked_days_1m'] or 0
    blocked = s['blocked_days_1m'] or 0
    avail = max(30 - blocked, 1)
    occ = booked / avail
    realized = round(sam_week * occ * 30 / 7, 1)

    bldg_rents = [nv['rent_monthly'] for nv in bldg_all if nv['rent_monthly']]
    bldg_cnt = len(bldg_all)

    # 대표역: station_500m_names JSON 첫 번째
    try:
        station = (__import__('json').loads(s['station_500m_names'] or '[]') or [''])[0]
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
        'pyeong': s['area_pyeong'] or '',
        'sam_week': sam_week,
        'sam_month': sam_month,
        'bk': booked,
        'bl': blocked,
        'realized': realized,
        'rent': rent,
        'dep': dep,
        'navmgmt': navmgmt,
        'mgmt_known': mgmt_known,
        'nav_tot': nav_tot,
        'n': len(use),
        'nv_bldg': rep['building_name'],
        'nv_url': nav_url,
        'eff': round(nav_tot / sam_week, 2) if sam_week else 0,
        'real_eff': round(realized / nav_tot, 2) if nav_tot else 0,
        'real_eff_rent': round(realized / rent, 2) if rent else 0,
        'net': round(realized - nav_tot, 1),
        'bldg_cnt': bldg_cnt,
        'bldg_rent_min': round(min(bldg_rents), 1) if bldg_rents else '',
        'bldg_rent_med': round(statistics.median(bldg_rents), 1) if bldg_rents else '',
        'bldg_rent_max': round(max(bldg_rents), 1) if bldg_rents else '',
    })

rows.sort(key=lambda x: x['net'], reverse=True)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow([
        '삼삼ID', '매물명', '건물유형', '방수', '시도', '시군구', '동', '인근역',
        '동삼삼매물수', '평수', '삼삼주당_만원', '삼삼월환산_만원',
        '1달예약일', '1달막힘일', '1달실현수익_만원',
        '네이버월세_만원', '네이버관리비_만원', '관리비표기여부',
        '네이버월총_만원', '네이버보증금_만원', '매칭매물수',
        '네이버월총÷삼삼주당', '실현효율(1달실현÷네이버월총)',
        '현실효율(1달실현÷네이버월세)', '순수익_만원(1달실현−월세−관리비)',
        '건물네이버매물수', '건물월세최저_만원', '건물월세중간_만원', '건물월세최고_만원',
        '네이버건물', '네이버링크', '삼삼링크',
    ])
    for r in rows:
        w.writerow([
            r['rid'], r['name'], r['btype'], r['rooms'],
            r['sido'], r['sigungu'], r['dong'], r['station'],
            r['sam_nearby'], r['pyeong'], r['sam_week'], r['sam_month'],
            r['bk'], r['bl'], r['realized'], r['rent'], r['navmgmt'],
            '표기' if r['mgmt_known'] else '미표기(평당2만)',
            r['nav_tot'], r['dep'], r['n'], r['eff'],
            r['real_eff'], r['real_eff_rent'], r['net'],
            r['bldg_cnt'], r['bldg_rent_min'], r['bldg_rent_med'], r['bldg_rent_max'],
            r['nv_bldg'], r['nv_url'],
            f"https://web.33m2.co.kr/guest/room/{r['rid']}",
        ])

print(f"통합 매칭: {len(rows)}건 → {OUT}")
print("건물유형:", Counter(r['btype'] for r in rows).most_common())
print("방수:", Counter(r['rooms'] for r in rows).most_common())
