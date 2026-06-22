# -*- coding: utf-8 -*-
"""
네이버 매물 관리비(mgmt) 보강 (전국, Supabase).
- 매칭 관여 매물의 상세 API /api/articles/{no} → monthlyManagementCost.
- crawler.NaverLand 재사용. 증분 저장(이어받기).
"""
import json, os, math, re, time, sys
from collections import defaultdict
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import db
import crawler
from crawler import NaverLand

DATA = os.path.join(BASE, "data")
PYEONG_M2 = 3.305785; R = 50; AREA_ABS = 3.0


def load_jsonl(path):
    by = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try: o = json.loads(line); by[o['rid']] = o
                except: pass
    return by


def norm(s):
    if not s: return ''
    s = re.sub(r'\(.*?\)', '', s); return re.sub(r'[^가-힣A-Za-z0-9]', '', s)
def bldg(addr):
    t = (addr or '').split()
    for i, x in enumerate(t):
        if any(ch.isdigit() for ch in x) and ('-' in x or x.replace('-', '').isdigit()):
            return ' '.join(t[i+1:]) if i+1 < len(t) else ''
    return t[-1] if t else ''
def dist(a, b, x, y): return math.hypot((a-x)*111000, (b-y)*88800)


def main():
    # 삼삼 유효매물
    off = load_jsonl(os.path.join(DATA, "officetel_raw.jsonl"))
    one = load_jsonl(os.path.join(DATA, "oneroom_raw.jsonl"))
    sam = list(off.values()) + [o for r, o in one.items() if r not in off]
    sam = [o for o in sam if o.get('lat') and o.get('lng') and o.get('pyeong') and (o.get('fee') or 0) > 0]

    # 네이버 전체 매물 인덱스 (Supabase에 수도권+비수도권 통합)
    con = db.connect()
    nav = [dict(r) for r in
           con.execute("SELECT articleNo,articleName,area_m2,lat,lon,dong FROM listings WHERE lat IS NOT NULL").fetchall()]
    fine = defaultdict(list); nidx = defaultdict(list)
    for nv in nav:
        nv['_n'] = norm(nv['articleName'])
        fine[(round(nv['lat'], 3), round(nv['lon'], 3))].append(nv)
        if nv['_n']: nidx[nv['dong']].append(nv)

    # 매칭 관여 articleNo 집합
    needed = set()
    for s in sam:
        slat, slon = float(s['lat']), float(s['lng']); s_m2 = float(s['pyeong'])*PYEONG_M2
        sb = norm(bldg(s.get('addr', ''))); ca, co = round(slat, 3), round(slon, 3)
        cands = {}
        for dla in (-0.001, 0, 0.001):
            for dlo in (-0.001, 0, 0.001):
                for nv in fine.get((round(ca+dla, 3), round(co+dlo, 3)), []):
                    if dist(slat, slon, nv['lat'], nv['lon']) <= R: cands[nv['articleNo']] = nv
        if sb and len(sb) >= 3:
            for nv in nidx.get(s.get('town', ''), []):
                if nv['_n'] == sb or (len(sb) >= 4 and (sb in nv['_n'] or nv['_n'] in sb)): cands[nv['articleNo']] = nv
        for no, nv in cands.items():
            if nv['area_m2'] and abs(nv['area_m2']-s_m2) <= AREA_ABS:
                needed.add(no)

    done = set(r[0] for r in con.execute("SELECT articleNo FROM listings WHERE mgmt IS NOT NULL").fetchall())
    todo = [a for a in needed if a not in done]
    print(f"매칭 관여 {len(needed)} / 완료 {len(done)} / 받을 것 {len(todo)}")
    if not todo:
        con.close(); print("보강할 것 없음"); return

    nl = NaverLand(headless=True)
    got = 0; t0 = time.time()
    try:
        for i, no in enumerate(todo, 1):
            j = nl.api(f"https://new.land.naver.com/api/articles/{no}?complexNo=")
            if j:
                m = (j.get('articleDetail') or {}).get('monthlyManagementCost')
                con.execute("UPDATE listings SET mgmt=? WHERE articleNo=?",
                            (m if m is not None else -1, no))
                con.commit(); got += 1
            if i % 100 == 0:
                rate = i/(time.time()-t0)
                print(f"[{datetime.now():%H:%M:%S}] [{i}/{len(todo)}] 저장 {got}, {rate:.1f}/s, ETA ~{(len(todo)-i)/rate:.0f}s")
            time.sleep(0.35)
            if i % 200 == 0:
                nl.restart()
    finally:
        nl.close()
        con.close()
    print(f"완료. 신규 {got}건 보강")


if __name__ == "__main__":
    main()
