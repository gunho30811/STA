# -*- coding: utf-8 -*-
"""
통합 수익성 분석: 삼삼(오피스텔+원룸 캐시) × 네이버(수도권 오피스텔).
- net_profit_strict.py 의 엄격매칭 로직을 그대로 쓰되,
  건물유형(오피스텔/기타) + 방수(원룸/투룸/쓰리룸+) 컬럼을 추가한다.
- 건물유형: officetel 캐시(propertyTypes=OFFICETEL로 수집)에 있으면 '오피스텔',
            아니면(oneroom 캐시에만 있으면) '기타(비오피스텔)'.
- 방수: roomCnt (1=원룸, 2=투룸, 3+=쓰리룸+)
출력: data/net_profit_integrated.csv
"""
import json, sqlite3, csv, os, math, re, statistics, sys
from datetime import datetime
from collections import defaultdict, Counter
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
OFF_JSONL = os.path.join(DATA, "officetel_raw.jsonl")
ONE_JSONL = os.path.join(DATA, "oneroom_raw.jsonl")
NAV_DB = os.path.join(DATA, "naver_opst_enriched.db")
OUT = os.path.join(DATA, "net_profit_integrated.csv")

START = datetime(2026, 6, 16).date(); END = datetime(2026, 7, 15).date()
WINDOW_DAYS = (END - START).days + 1
PYEONG_M2 = 3.305785; WEEKS = 4.345; R = 50; AREA_ABS = 3.0; PURE_DEP_MAN = 2000


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

# ── 네이버(전국 오피스텔) 인덱스: 수도권 enriched(mgmt有) + 비수도권(mgmt無) 병합 ──
NONSEOUL_DB = os.path.join(DATA, "naver_nonseoul.db")

def load_nav(path):
    if not os.path.exists(path):
        return []
    c = sqlite3.connect(path); c.row_factory = sqlite3.Row
    cols = [r[1] for r in c.execute("PRAGMA table_info(listings)")]
    has_mgmt = "mgmt" in cols
    sel = "articleNo,articleName,area_m2,rent,deposit,floorInfo,lat,lon,dong"
    sel += ",mgmt" if has_mgmt else ""
    out = []
    for r in c.execute(f"SELECT {sel} FROM listings WHERE rent>0 AND lat IS NOT NULL"):
        d = dict(r)
        if not has_mgmt:
            d["mgmt"] = -1          # 미표기 처리(매칭 시 평당2만 추정)
        out.append(d)
    c.close()
    return out

nav = load_nav(NAV_DB) + load_nav(NONSEOUL_DB)
# articleNo 중복 제거(혹시 모를 겹침)
_seen = {}
for nv in nav:
    _seen[nv["articleNo"]] = nv
nav = list(_seen.values())
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
    nv['_n'] = norm(nv['articleName'])
    fine[(round(nv['lat'], 3), round(nv['lon'], 3))].append(nv)
    if nv['_n']: nidx[nv['dong']].append(nv)
def dist(a, b, x, y): return math.hypot((a-x)*111000, (b-y)*88800)

def strict_match(s):
    slat, slon = float(s['lat']), float(s['lng']); s_m2 = float(s['pyeong'])*PYEONG_M2
    sb = norm(bldg(s.get('addr', '')))
    cands = {}
    ca, co = round(slat, 3), round(slon, 3)
    for dla in (-0.001, 0, 0.001):
        for dlo in (-0.001, 0, 0.001):
            for nv in fine.get((round(ca+dla, 3), round(co+dlo, 3)), []):
                if dist(slat, slon, nv['lat'], nv['lon']) <= R: cands[id(nv)] = nv
    if sb and len(sb) >= 3:
        for nv in nidx.get(s.get('town', ''), []):
            if nv['_n'] == sb or (len(sb) >= 4 and (sb in nv['_n'] or nv['_n'] in sb)): cands[id(nv)] = nv
    return [nv for nv in cands.values() if nv['area_m2'] and abs(nv['area_m2']-s_m2) <= AREA_ABS]

rows = []
for s in valid:
    hits = strict_match(s)
    if not hits: continue
    pure = [h for h in hits if (h['deposit'] or 0) <= PURE_DEP_MAN]
    use = pure if pure else sorted(hits, key=lambda h: (h['deposit'] or 0))[:1]
    rent = statistics.median([h['rent'] for h in use])
    dep = statistics.median([(h['deposit'] or 0) for h in use])
    mgs = [h['mgmt'] for h in hits if h['mgmt'] not in (None, -1) and h['mgmt'] > 0]
    navmgmt = man(statistics.median(mgs)) if mgs else round(int(s['pyeong'])*2.0, 1)
    mgmt_known = 1 if mgs else 0
    rep = min(use, key=lambda h: abs(h['rent']-rent))
    nv_url = f"https://new.land.naver.com/offices?articleNo={rep['articleNo']}"
    nav_tot = rent + navmgmt
    sam_week = man((s.get('fee') or 0) + (s.get('mgmt') or 0))
    sam_month = round(sam_week*WEEKS, 1)
    # 막힘일(호스트가 의도적으로 막은 날)은 "운영 불가 기간"으로 보고 점유율 계산에서 제외.
    # 점유율 = 예약일/(전체기간-막힘일) 을 한 달(WINDOW_DAYS) 전체에 적용해서 실현수익 추정.
    avail_days = WINDOW_DAYS - s['ds']
    occ_rate = (s['bk']/avail_days) if avail_days > 0 else 0
    realized = round(sam_week*(occ_rate*WINDOW_DAYS)/7, 1)
    dong_key = (s.get('state', ''), s.get('province', ''), s.get('town', ''))
    sam_nearby = dong_count[dong_key] - 1
    rows.append({
        'rid': s['rid'], 'name': s.get('name', ''), 'btype': s['btype'], 'rooms': s['rooms'],
        'sido': s.get('state', ''), 'station': s.get('station', ''), 'sam_nearby': sam_nearby,
        'prov': s.get('province', ''), 'town': s.get('town', ''), 'pyeong': int(s['pyeong']),
        'sam_week': sam_week, 'sam_month': sam_month, 'bk': s['bk'], 'ds': s['ds'],
        'realized': realized, 'rent': rent, 'dep': dep, 'navmgmt': navmgmt, 'nav_tot': nav_tot,
        'n': len(use), 'mgmt_known': mgmt_known, 'nv_url': nv_url, 'nv_bldg': rep['articleName'],
        'eff': round(nav_tot/sam_week, 2) if sam_week else 0,
        'real_eff': round(realized/nav_tot, 2) if nav_tot else 0,
        'real_eff_rent': round(realized/rent, 2) if rent else 0,
        'net': round(realized-nav_tot, 1)})

rows.sort(key=lambda x: x['net'], reverse=True)
with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['삼삼ID', '매물명', '건물유형', '방수', '시도', '시군구', '동', '인근역', '동삼삼매물수', '평수',
                '삼삼주당_만원', '삼삼월환산_만원',
                '1달예약일', '1달막힘일', '1달실현수익_만원', '네이버월세_만원', '네이버관리비_만원', '관리비표기여부',
                '네이버월총_만원', '네이버보증금_만원', '매칭매물수', '네이버월총÷삼삼주당',
                '실현효율(1달실현÷네이버월총)', '현실효율(1달실현÷네이버월세)', '순수익_만원(1달실현−월세−관리비)',
                '네이버건물', '네이버링크', '삼삼링크'])
    for r in rows:
        w.writerow([r['rid'], r['name'], r['btype'], r['rooms'], r['sido'], r['prov'], r['town'],
                    r['station'], r['sam_nearby'], r['pyeong'],
                    r['sam_week'], r['sam_month'], r['bk'], r['ds'], r['realized'], r['rent'], r['navmgmt'],
                    ('표기' if r['mgmt_known'] else '미표기(평당2만)'), r['nav_tot'], r['dep'], r['n'], r['eff'],
                    r['real_eff'], r['real_eff_rent'], r['net'], r['nv_bldg'], r['nv_url'],
                    f"https://web.33m2.co.kr/guest/room/{r['rid']}"])

print(f"통합 매칭 결과: {len(rows)}개 → {OUT}")
print("건물유형:", Counter(r['btype'] for r in rows).most_common())
print("방수:", Counter(r['rooms'] for r in rows).most_common())
