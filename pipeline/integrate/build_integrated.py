# -*- coding: utf-8 -*-
"""
통합 수익성 분석: 삼삼(오피스텔+원룸 캐시) × 네이버(전국, Supabase).
- 건물유형: officetel 캐시에 있으면 '오피스텔', oneroom 캐시에만 있으면 '기타(비오피스텔)'.
- 방수: roomCnt (1=원룸, 2=투룸, 3+=쓰리룸+)
출력: data/net_profit_integrated.csv
"""
import json, csv, os, math, re, statistics, sys
from datetime import datetime
from collections import defaultdict, Counter
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
import db

DATA = os.path.join(BASE, "data")
OFF_JSONL = os.path.join(DATA, "officetel_raw.jsonl")
ONE_JSONL = os.path.join(DATA, "oneroom_raw.jsonl")
OUT = os.path.join(DATA, "net_profit_integrated.csv")

START = datetime(2026, 6, 16).date(); END = datetime(2026, 7, 15).date()
WINDOW_DAYS = (END - START).days + 1
PYEONG_M2 = 3.305785; WEEKS = 4.345; R = 50; AREA_PCT = 0.15; PURE_DEP_MAN = 2000


def inwin(d):
    try: return START <= datetime.strptime(d, '%Y-%m-%d').date() <= END
    except: return False
def man(won): return round((won or 0) / 10000, 1)


def load_jsonl(path):
    by = {}
    if not os.path.exists(path): return by
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: o = json.loads(line)
            except: continue
            if 'rid' in o: by[o['rid']] = o
    return by


def room_label(rc):
    try: rc = int(rc)
    except: return '기타'
    return {1: '원룸', 2: '투룸'}.get(rc, '쓰리룸+')


# ── 삼삼 매물 통합(건물유형/방수 분류) ──
# 건물유형 맵(classify_btype.py 산출): {rid: 'APARTMENT'|'VILLA'|'HOUSE'|...}
BTYPE_KO = {'APARTMENT': '아파트', 'VILLA': '빌라', 'HOUSE': '주택',
            'OFFICE': '사무실', 'STORE': '상가'}
btmap = {}
_bt_path = os.path.join(DATA, "btype_map.json")
if os.path.exists(_bt_path):
    btmap = json.load(open(_bt_path, encoding='utf-8'))

off = load_jsonl(OFF_JSONL)        # 전부 오피스텔
one = load_jsonl(ONE_JSONL)        # 방수=1, 건물유형 섞임
sam = []
for rid, o in off.items():
    o['btype'] = '오피스텔'         # officetel 캐시 → 확정
    sam.append(o)
for rid, o in one.items():
    if rid in off: continue        # 오피스텔로 이미 포함(중복 제거)
    o['btype'] = BTYPE_KO.get(btmap.get(str(rid)), '기타(비오피스텔)')
    sam.append(o)

# 유효성 + 1달 예약/막힘일 계산
valid = []
for o in sam:
    if not (o.get('lat') and o.get('lng') and o.get('pyeong') and (o.get('fee') or 0) > 0):
        continue
    b = d = 0
    for dt, st in (o.get('schedules') or {}).items():
        if not inwin(dt): continue
        if st == 'booking': b += 1
        elif st in ('disable', 'disabled', 'blocked'): d += 1
    o['bk'] = b; o['ds'] = d
    o['rooms'] = room_label(o.get('roomCnt'))
    valid.append(o)
print(f"삼삼 통합 유효매물: {len(valid)} (오피스텔 {sum(1 for x in valid if x['btype']=='오피스텔')}, 기타 {sum(1 for x in valid if x['btype']!='오피스텔')})")

# 동 단위 삼삼 매물수 (자기 자신 제외) — "근처에 경쟁자가 몇 개인지"
dong_count = Counter((o.get('state', ''), o.get('province', ''), o.get('town', '')) for o in valid)

# ── 네이버(전국 오피스텔) 인덱스: Supabase 단일 쿼리 ──
def load_nav():
    conn = db.connect()
    # naver_listings 는 이제 6개 타입(아파트/오피스텔/빌라/원룸/단독다가구/상가)이 섞여 들어올 수 있음.
    # 삼삼 쪽은 오피스텔/원룸 단기임대만 다루므로, 엉뚱한 타입(아파트/상가 등)과 잘못 매칭되지 않도록
    # 오피스텔만 사용 (기존 동작 유지). TODO: 삼삼 btype_map 의 비오피스텔 분류와 맞춰 타입별 매칭 확장.
    out = [dict(r) for r in conn.execute(
        "SELECT article_no,url,building_name,area_exclusive_m2,rent_monthly,deposit,"
        "maintenance_monthly,floor_current,lat,lng,dong"
        " FROM naver_listings WHERE rent_monthly BETWEEN 5 AND 2000 AND lat IS NOT NULL"
        " AND building_type_code = 'OPST'"
    ).fetchall()]
    conn.close()
    return out

nav = load_nav()
print(f"네이버 매물(전국): {len(nav)}")
def norm(s):
    if not s: return ''
    s = re.sub(r'\(.*?\)', '', s); return re.sub(r'[^가-힣A-Za-z0-9]', '', s)
def bldg(addr):
    t = (addr or '').split()
    for i, x in enumerate(t):
        if any(ch.isdigit() for ch in x) and ('-' in x or x.replace('-', '').isdigit()):
            return ' '.join(t[i+1:]) if i+1 < len(t) else ''
    return t[-1] if t else ''
fine = defaultdict(list); nidx = defaultdict(list)
for nv in nav:
    nv['_n'] = norm(nv['building_name'])
    fine[(round(nv['lat'], 3), round(nv['lng'], 3))].append(nv)
    if nv['_n']: nidx[nv['dong']].append(nv)
def dist(a, b, x, y): return math.hypot((a-x)*111000, (b-y)*88800)


def _parse_sam_floor(name):
    """삼삼 name에서 'N층' 숫자 추출"""
    m = re.search(r'(\d+)층', name or '')
    return int(m.group(1)) if m else None


def _floor_ok(floor_current, sam_floor):
    """층 호환 여부. 한쪽이라도 층 정보 없으면 True(통과)."""
    if not sam_floor or floor_current is None:
        return True
    return abs(floor_current - sam_floor) <= 3


def strict_match(s):
    """(매칭 매물 리스트, 건물 전체 매물 리스트) 반환.

    전략:
    - 삼삼에 건물명 있으면 → 건물명 매칭만 사용 (좌표 500m 이내 sanity check 병행)
    - 삼삼에 건물명 없으면 → 좌표 15m 이내(같은 건물 GPS 오차 범위)만 허용
    좌표 50m 단독 사용하지 않음 → 인접 건물 오매칭 방지.
    """
    slat, slon = float(s['lat']), float(s['lng']); s_m2 = float(s['pyeong'])*PYEONG_M2
    sb = norm(bldg(s.get('addr', '')))
    sam_floor = _parse_sam_floor(s.get('name', ''))
    cands = {}

    if sb and len(sb) >= 3:
        # 건물명 매칭: 같은 동 내 이름 일치 + 500m 이내 sanity
        for nv in nidx.get(s.get('town', ''), []):
            if nv['_n'] == sb or (len(sb) >= 4 and (sb in nv['_n'] or nv['_n'] in sb)):
                if dist(slat, slon, nv['lat'], nv['lng']) <= 500:
                    cands[id(nv)] = nv
    else:
        # 건물명 없음: GPS 15m 이내만 (같은 건물 출입구 오차 수준)
        ca, co = round(slat, 3), round(slon, 3)
        for dla in (-0.001, 0, 0.001):
            for dlo in (-0.001, 0, 0.001):
                for nv in fine.get((round(ca+dla, 3), round(co+dlo, 3)), []):
                    if dist(slat, slon, nv['lat'], nv['lng']) <= 15:
                        cands[id(nv)] = nv

    bldg_all = list(cands.values())
    # 면적 ±15% 상대값 필터
    area_ok = [nv for nv in bldg_all if nv['area_exclusive_m2'] and abs(nv['area_exclusive_m2']-s_m2)/s_m2 <= AREA_PCT]
    # 층수 필터 (둘 다 층 정보 있을 때만)
    hits = [nv for nv in area_ok if _floor_ok(nv.get('floor_current'), sam_floor)]
    return hits, bldg_all

rows = []
for s in valid:
    hits, bldg_all = strict_match(s)
    if not hits: continue
    pure = [h for h in hits if (h['deposit'] or 0) <= PURE_DEP_MAN]
    use = pure if pure else sorted(hits, key=lambda h: (h['deposit'] or 0))[:1]
    rent = statistics.median([h['rent_monthly'] for h in use])
    dep = statistics.median([(h['deposit'] or 0) for h in use])
    mgs = [h['maintenance_monthly'] for h in hits if h['maintenance_monthly'] not in (None, -1) and h['maintenance_monthly'] > 0]
    navmgmt = statistics.median(mgs) if mgs else round(int(s['pyeong'])*2.0, 1)
    mgmt_known = 1 if mgs else 0
    rep = min(use, key=lambda h: abs(h['rent_monthly']-rent))
    nv_url = rep.get('url') or f"https://new.land.naver.com/offices?articleNo={rep['article_no']}"
    nav_tot = rent + navmgmt
    sam_week = man((s.get('fee') or 0) + (s.get('mgmt') or 0))
    sam_month = round(sam_week*WEEKS, 1)
    avail_days = WINDOW_DAYS - s['ds']
    occ_rate = (s['bk']/avail_days) if avail_days > 0 else 0
    realized = round(sam_week*(occ_rate*WINDOW_DAYS)/7, 1)
    dong_key = (s.get('state', ''), s.get('province', ''), s.get('town', ''))
    sam_nearby = dong_count[dong_key] - 1
    # 건물 전체 통계 (면적·층수 필터 전 bldg_all 기준)
    bldg_rents = [nv['rent_monthly'] for nv in bldg_all if nv['rent_monthly']]
    bldg_cnt = len(bldg_all)
    bldg_rent_min = round(min(bldg_rents), 1) if bldg_rents else ''
    bldg_rent_max = round(max(bldg_rents), 1) if bldg_rents else ''
    bldg_rent_med = round(statistics.median(bldg_rents), 1) if bldg_rents else ''
    rows.append({
        'rid': s['rid'], 'name': s.get('name', ''), 'btype': s['btype'], 'rooms': s['rooms'],
        'sido': s.get('state', ''), 'station': s.get('station', ''), 'sam_nearby': sam_nearby,
        'prov': s.get('province', ''), 'town': s.get('town', ''), 'pyeong': int(s['pyeong']),
        'sam_week': sam_week, 'sam_month': sam_month, 'bk': s['bk'], 'ds': s['ds'],
        'realized': realized, 'rent': rent, 'dep': dep, 'navmgmt': navmgmt, 'nav_tot': nav_tot,
        'n': len(use), 'mgmt_known': mgmt_known, 'nv_url': nv_url, 'nv_bldg': rep['building_name'],
        'eff': round(nav_tot/sam_week, 2) if sam_week else 0,
        'real_eff': round(realized/nav_tot, 2) if nav_tot else 0,
        'real_eff_rent': round(realized/rent, 2) if rent else 0,
        'net': round(realized-nav_tot, 1),
        'bldg_cnt': bldg_cnt, 'bldg_rent_min': bldg_rent_min,
        'bldg_rent_med': bldg_rent_med, 'bldg_rent_max': bldg_rent_max})

rows.sort(key=lambda x: x['net'], reverse=True)
with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['삼삼ID', '매물명', '건물유형', '방수', '시도', '시군구', '동', '인근역', '동삼삼매물수', '평수',
                '삼삼주당_만원', '삼삼월환산_만원',
                '1달예약일', '1달막힘일', '1달실현수익_만원', '네이버월세_만원', '네이버관리비_만원', '관리비표기여부',
                '네이버월총_만원', '네이버보증금_만원', '매칭매물수', '네이버월총÷삼삼주당',
                '실현효율(1달실현÷네이버월총)', '현실효율(1달실현÷네이버월세)', '순수익_만원(1달실현−월세−관리비)',
                '건물네이버매물수', '건물월세최저_만원', '건물월세중간_만원', '건물월세최고_만원',
                '네이버건물', '네이버링크', '삼삼링크'])
    for r in rows:
        w.writerow([r['rid'], r['name'], r['btype'], r['rooms'], r['sido'], r['prov'], r['town'],
                    r['station'], r['sam_nearby'], r['pyeong'],
                    r['sam_week'], r['sam_month'], r['bk'], r['ds'], r['realized'], r['rent'], r['navmgmt'],
                    ('표기' if r['mgmt_known'] else '미표기(평당2만)'), r['nav_tot'], r['dep'], r['n'], r['eff'],
                    r['real_eff'], r['real_eff_rent'], r['net'],
                    r['bldg_cnt'], r['bldg_rent_min'], r['bldg_rent_med'], r['bldg_rent_max'],
                    r['nv_bldg'], r['nv_url'],
                    f"https://web.33m2.co.kr/guest/room/{r['rid']}"])

print(f"통합 매칭 결과: {len(rows)}개 → {OUT}")
print("건물유형:", Counter(r['btype'] for r in rows).most_common())
print("방수:", Counter(r['rooms'] for r in rows).most_common())
