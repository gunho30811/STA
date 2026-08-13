# -*- coding: utf-8 -*-
"""
통합 포털: 로그인 게이트 + 랜딩 + 3개 뷰어를 한 주소에 마운트.

  /            랜딩(로그인 필요) — 각 뷰어 링크
  /profit/...  통합 수익성(profit_app)
  /samsam/...  렌트 분석(samsam_app)
  /gangnam/... 부동산 강남 매물(gangnam_app)
  /auth/...    로그인/가입/회원관리

로컬:  python web/portal.py   → http://127.0.0.1:8000
Vercel: api/index.py 가 application(WSGI) 을 가져다 씀.
쿠키 path=/ 라 한 번 로그인하면 모든 마운트에서 공유된다.
"""
import datetime as dt
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "common"))   # subway(역 좌표) 유틸
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # web/

from flask import Flask, render_template_string
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix

import db
import poi as poi_mod
import subway
import target_regions   # 크롤·노출 대상 지역(수도권 + 부산·천안)
from auth import current_user, init_auth, latest_listing_churn, online_count

portal = Flask(__name__)
# 데모 공개: 계산기(calc)는 완전 무료, 지도(fullmap)는 비로그인 시 강남권만 미리보기.
init_auth(portal, demo_endpoints={"calc", "fullmap"})

# ── 대시보드 인사이트: 반도체 공장 인근 · 1기 신도시 렌트 예약률 ──────────────────
# 공장 반경 3km(1km 안은 공단이라 매물이 없음 → 통근 주거권), 신도시 반경 1.5km.
FACTORIES = [   # (표시명, lat, lng)
    ("삼성 평택캠퍼스", 37.0075, 127.0620),
    ("삼성 기흥캠퍼스", 37.2405, 127.0533),
    ("삼성 화성캠퍼스", 37.2216, 127.0116),
    ("SK하이닉스 이천", 37.2733, 127.5116),
    ("SK하이닉스 청주", 36.6435, 127.4890),
]
NEWTOWNS = [    # 1기 신도시 중심 좌표
    ("분당", 37.3849, 127.1230),
    ("일산", 37.6588, 126.7730),
    ("평촌", 37.3898, 126.9506),
    ("산본", 37.3583, 126.9330),
    ("중동", 37.5046, 126.7630),
]
_INS_CACHE = {"t": 0.0, "data": None}


def _spot_stats(rows, lat, lng, km):
    """rows(lat,lng,bk,bl) 중 반경 km 내 매물수·평균 예약률(%)."""
    occs = []
    for la, ln, bk, bl in rows:
        if math.hypot((la - lat) * 111, (ln - lng) * 88.8) <= km:
            occs.append(min(1.0, (bk or 0) / max(31 - (bl or 0), 1)))
    return len(occs), (round(sum(occs) / len(occs) * 100, 1) if occs else None)


# 공급부족 스팟 기준 — 이 서비스의 핵심 신호.
# 수요高(예약률↑) + 경쟁低(렌트 매물 적음) + 진입가능(네이버 월세 물건 존재) 동네를 찾는다.
SPOT_MIN_OCC = 55     # 예약률 하한(%)
SPOT_MIN_RENT = 5     # 렌트 매물 표본 하한(신뢰도)
SPOT_MAX_RENT = 40    # 렌트 매물 상한(이보다 많으면 이미 경쟁 시장)
SPOT_TOP = 8


def _supply_shortage_spots(conn):
    """동 단위 공급부족 스팟 TOP N — 예약률·렌트/네이버 매물수·평균 월순수익·영업이익률."""
    # ① 동별 렌트 수요/공급
    where, params = target_regions.sql_where()
    dongs = conn.execute(
        "SELECT sigungu, dong, COUNT(*) n,"
        " AVG(LEAST(1.0, COALESCE(booked_days_1m,0)::float"
        "     / GREATEST(31-COALESCE(blocked_days_1m,0),1)))*100 occ"
        f" FROM samsam_listings WHERE {where}"
        " GROUP BY sigungu, dong HAVING COUNT(*) >= %s",
        params + [SPOT_MIN_RENT]).fetchall()
    cands = [{"sigungu": r[0] or "", "dong": r[1] or "", "n_rent": r[2], "occ": round(r[3], 1)}
             for r in dongs if r[3] >= SPOT_MIN_OCC and r[2] <= SPOT_MAX_RENT]
    cands.sort(key=lambda x: -x["occ"])
    cands = cands[:SPOT_TOP]
    if not cands:
        return []
    dong_names = list({c["dong"] for c in cands})

    # ② 동별 수익성(net_profit 매칭분): 실현매출=최대매출×예약률, 순수익=실현−네이버월총, 이익률=순/실현
    prof = {}
    for sg, d, bk, bl, mx, nt in conn.execute(
            "SELECT sigungu, dong, bk, bl, maxrev, ntotal FROM net_profit"
            " WHERE ntotal IS NOT NULL AND maxrev IS NOT NULL AND dong = ANY(%s)",
            (dong_names,)).fetchall():
        occ = min(1.0, (bk or 0) / max(31 - (bl or 0), 1))
        rev = (mx or 0) * occ
        g = prof.setdefault((sg or "", d or ""), [0.0, 0.0, 0])
        g[0] += rev; g[1] += rev - (nt or 0); g[2] += 1

    # ③ 동별 네이버 월세 물건 수(진입 가능한 매물)
    nav = {}
    try:
        for sg, d, n in conn.execute(
                "SELECT sigungu, dong, COUNT(*) FROM nl_live"
                " WHERE rent_monthly IS NOT NULL AND dong = ANY(%s) GROUP BY sigungu, dong",
                (dong_names,)).fetchall():
            nav[(sg or "", d or "")] = n
    except Exception:
        pass

    # 동 대표 좌표(중앙값 — 불량 좌표 내성) → 근처 수요시설(왜 수요가 있나의 근거)
    coords = {}
    for sg, d, la, ln in conn.execute(
            "SELECT sigungu, dong, lat, lng FROM samsam_listings"
            " WHERE dong = ANY(%s) AND lat BETWEEN 33 AND 39.5 AND lng BETWEEN 124 AND 132",
            (dong_names,)).fetchall():
        coords.setdefault((sg or "", d or ""), []).append((la, ln))

    for c in cands:
        key = (c["sigungu"], c["dong"])
        g = prof.get(key)
        c["net"] = round(g[1] / g[2], 1) if g and g[2] else None          # 평균 월순수익(만원)
        c["margin"] = round(g[1] / g[0] * 100, 1) if g and g[0] > 0 else None  # 영업이익률(%)
        c["n_naver"] = nav.get(key, 0)
        pts = coords.get(key) or []
        if pts:
            lats = sorted(p[0] for p in pts); lngs = sorted(p[1] for p in pts)
            m = len(pts) // 2
            c["poi"] = poi_mod.nearby(lats[m], lngs[m], 2.5, 2)
        else:
            c["poi"] = []
    cost = _entry_cost(conn, dong_names)
    for c in cands:
        c["cost"] = cost.get((c["sigungu"], c["dong"]))
    return cands


def _entry_cost(conn, dong_names):
    """동별 진입 원가 — 주거용 소형(전용 20~60㎡, 월세 20~200만) 월세·보증금 중앙값.
    '이 동네에 단기임대로 들어가려면 얼마 드나' = 스팟 판단의 마지막 조각.
    상가·대형 평수가 섞이면 중앙값이 튀므로(월세 350만 등) 유형·면적으로 좁힌다."""
    out = {}
    if not dong_names:
        return out
    try:
        # percentile_cont 를 두 번 쓰면 결과 컬럼명이 둘 다 'percentile_cont' 라 이름 기반 행
        # 매핑에서 뒤 값이 앞을 덮어쓴다 → 반드시 별칭(rent_med/dep_med)으로 구분.
        for r in conn.execute(
                "SELECT sigungu, dong, COUNT(*) AS n,"
                " percentile_cont(0.5) WITHIN GROUP (ORDER BY rent) AS rent_med,"
                " percentile_cont(0.5) WITHIN GROUP (ORDER BY deposit) AS dep_med"
                " FROM listings WHERE dong = ANY(%s)"
                "   AND realestatetype IN ('오피스텔','원룸','빌라','아파트','단독/다가구')"
                "   AND rent BETWEEN 20 AND 200 AND area_real_m2 BETWEEN 20 AND 60"
                " GROUP BY sigungu, dong", (dong_names,)).fetchall():
            sg, d, n, rent, dep = r[0], r[1], r[2], r[3], r[4]
            if n >= 3 and rent is not None and dep is not None:
                out[(sg or "", d or "")] = {"n": n, "rent": round(rent), "dep": round(dep)}
    except Exception:
        pass
    return out


def _unclaimed_spots(conn):
    """미개척 지역 — 네이버 월세 회전율(=수요 신호, 삼삼 없이도 계산 가능)은 높은데
    삼삼(단기임대) 공급이 거의 없는 동. 삼삼 예약률 기반 스팟이 놓치는, '삼삼이 아직
    발 안 디딘' 지역을 잡아낸다(Phase 1 — 외부 데이터 연계 전 자체 데이터 버전).

    회전율 = 최근 7일 신규 등록(confirmYmd) / 활성 매물 수. 등록이 빨리 채워질수록
    월세 수요가 활발하다는 신호(매물이 안 나가면 신규 등록만 계속 쌓여 오히려 활성 재고가
    불어나므로, 회전율은 '재고 대비 신규 유입 속도'로 시장 활력의 근사치)."""
    # listings(네이버)는 시도 표기가 '서울시/인천시'라 삼삼 표기로 필터하면 경기도만 걸린다
    # → target_regions의 접두 매칭 사용.
    where, params = target_regions.sql_where()
    rows = conn.execute(
        "SELECT sigungu, dong,"
        " COUNT(*) FILTER (WHERE confirmymd IS NOT NULL) AS active,"
        " COUNT(*) FILTER (WHERE confirmymd::text >= to_char(now()-interval '7 days','YYYYMMDD')) AS new7"
        f" FROM listings WHERE {where} AND dong IS NOT NULL"
        " GROUP BY sigungu, dong HAVING COUNT(*) FILTER (WHERE confirmymd IS NOT NULL) >= 30",
        params).fetchall()
    dong_names = list({r[1] for r in rows})
    sam = dict(conn.execute(
        f"SELECT dong, COUNT(*) FROM samsam_listings WHERE {where}"
        " AND dong = ANY(%s) GROUP BY dong",
        params + [dong_names]).fetchall()) if dong_names else {}
    cands = []
    for sg, d, active, new7 in rows:
        turnover = new7 / active if active else 0
        samn = sam.get(d, 0)
        if turnover >= 0.15 and samn <= 2:
            cands.append({"sigungu": sg or "", "dong": d or "", "turnover": round(turnover * 100, 1),
                          "active": active, "new7": new7, "n_samsam": samn})
    cands.sort(key=lambda x: -x["turnover"])
    cands = cands[:SPOT_TOP]

    # 삼삼 데이터가 없는 동네라 '왜 수요가 있나'의 근거가 특히 중요 → 네이버 매물 좌표로 근처 수요시설.
    if cands:
        top_dongs = [c["dong"] for c in cands]
        coords = {}
        for sg, d, la, ln in conn.execute(
                "SELECT sigungu, dong, lat, lon FROM listings"
                " WHERE dong = ANY(%s) AND lat BETWEEN 33 AND 39.5 AND lon BETWEEN 124 AND 132",
                (top_dongs,)).fetchall():
            coords.setdefault((sg or "", d or ""), []).append((la, ln))
        cost = _entry_cost(conn, top_dongs)
        for c in cands:
            pts = coords.get((c["sigungu"], c["dong"])) or []
            if pts:
                lats = sorted(p[0] for p in pts); lngs = sorted(p[1] for p in pts)
                m = len(pts) // 2
                c["poi"] = poi_mod.nearby(lats[m], lngs[m], 3.0, 2)
            else:
                c["poi"] = []
            c["cost"] = cost.get((c["sigungu"], c["dong"]))
    return cands


def _compute_insights(conn):
    """무거운 계산(20초+) — 공장·신도시 예약률 + 공급부족 스팟 + 미개척 지역.
    요청 경로에서 직접 부르지 말 것. refresh_insights.py(크롤 후·크론)가 호출해 DB에 저장한다."""
    rows = [(r[0], r[1], r[2], r[3]) for r in conn.execute(
        "SELECT lat, lng, booked_days_1m, blocked_days_1m FROM samsam_listings"
        " WHERE lat IS NOT NULL").fetchall()]
    factories = []
    for name, la, ln in FACTORIES:
        n, occ = _spot_stats(rows, la, ln, 3.0)
        factories.append({"name": name, "n": n, "occ": occ})
    newtowns = []
    for name, la, ln in NEWTOWNS:
        n, occ = _spot_stats(rows, la, ln, 1.5)
        sts = subway.stations_within(la, ln, 1200)[:3]   # 중심 1.2km 내 역 최대 3개
        newtowns.append({"name": name, "n": n, "occ": occ, "stations": sts})
    spots = _supply_shortage_spots(conn)
    unclaimed = _unclaimed_spots(conn)
    return {"factories": factories, "newtowns": newtowns, "spots": spots, "unclaimed": unclaimed}


def dashboard_insights():
    """대시보드 인사이트 — 미리 계산해 DB(kv_cache)에 저장된 결과를 즉시 읽는다.
    프로세스 메모리 5분 캐시로 DB 왕복도 줄인다. DB에 없으면(최초) 그때만 계산해 저장(느림 1회).
    나이 무관하게 반환 — 데이터는 하루 1~2회 크롤이라 실시간성 불필요. 실패 시 None(섹션 생략)."""
    now = time.time()
    if _INS_CACHE["data"] is not None and now - _INS_CACHE["t"] < 300:
        return _INS_CACHE["data"]
    try:
        conn = db.connect()
        try:
            row = conn.execute("SELECT data FROM kv_cache WHERE k = %s",
                               ("dashboard_insights",)).fetchone()
        except Exception:
            row = None
        if row and row[0]:
            data = json.loads(row[0])
        else:
            # 캐시 미스(최초 배포 등) — 이번 한 번만 계산해 저장. 이후는 DB 히트.
            data = _compute_insights(conn)
            try:
                conn.execute(
                    "INSERT INTO kv_cache(k,data,updated_at) VALUES(%s,%s,%s)"
                    " ON CONFLICT (k) DO UPDATE SET data=EXCLUDED.data,updated_at=EXCLUDED.updated_at",
                    ("dashboard_insights", json.dumps(data, ensure_ascii=False),
                     dt.datetime.now().isoformat(timespec="seconds")))
                conn.commit()
            except Exception:
                pass
        # 신규 매물 급증 동네(크롤 prune_and_track이 kv_cache에 적재) — 별도 키라 여기서 병합.
        try:
            nr = conn.execute("SELECT data FROM kv_cache WHERE k = %s",
                              ("samsam_new_by_dong",)).fetchone()
            data["newlistings"] = json.loads(nr[0]) if nr and nr[0] else None
        except Exception:
            data["newlistings"] = None
        conn.close()
        _INS_CACHE.update(t=now, data=data)
        return data
    except Exception:
        return None


# ── 계산기 '나는 얼마나 벌고 있을까요?' 시장 비교 실데이터 ──────────────────────
# net_profit(삼삼×네이버 매칭 결과)의 기대 월순수익 분포. 정의는 /profit 페이지와 동일:
#   예약률 = 예약일 / (31 - 막힘일),  실현매출 = 최대수익 × 예약률,
#   기대 월순수익 = 실현매출 - 네이버월총(환산월세+관리비)
# 시안 단계에선 하드코딩 예시 숫자를 썼는데, 실제 분포와 크게 달라(중앙값 52만 vs 실제 3~13만)
# 그대로 두면 사용자를 오도한다 → 여기서 계산해 넣는다.
_MARKET_CACHE = {"t": 0.0, "data": None}
MARKET_MIN_N = 30          # 이보다 표본이 적은 조합은 내보내지 않는다(JS가 전국으로 폴백)
MARKET_MIN_OCC = 20        # 화면 문구와 동일: 예약률 20% 이상만
# (라벨, 하한, 상한) — 하한/상한 None = 열린 구간. 실제 분포가 적자까지 걸쳐 있어 음수 구간 포함.
MARKET_BINS = [("적자", None, 0), ("0~20만원", 0, 20), ("20만원대", 20, 40), ("40만원대", 40, 60),
               ("60만원대", 60, 80), ("80만원대", 80, 100), ("100만원+", 100, None)]
MARKET_TYPES = {"원룸": {"원룸건물"},
                "빌라·주택": {"연립빌라", "단독주택", "상가주택"},
                "오피스텔": {"오피스텔"}}
MARKET_REGIONS = [("전국", None), ("서울", "서울"), ("경기", "경기"),
                  ("인천", "인천"), ("부산", "부산")]


def _pct(sorted_vals, q):
    """선형보간 분위수(numpy 없이) — 표본이 적어도 안정적."""
    if not sorted_vals:
        return None
    i = (len(sorted_vals) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def market_stats():
    """{'data': {'서울 · 원룸': {bins,total,p25,median,p75}}, 'regions': [...], 'asof': 'YYYY-MM-DD'}"""
    now = time.time()
    if _MARKET_CACHE["data"] is not None and now - _MARKET_CACHE["t"] < 600:
        return _MARKET_CACHE["data"]
    out = {"data": {}, "regions": ["전국"], "asof": ""}
    try:
        conn = db.connect()
        rows = conn.execute(
            "SELECT sido, btype,"
            " maxrev * LEAST(100, bk / GREATEST(31 - COALESCE(bl,0), 1) * 100) / 100 - ntotal"
            " FROM net_profit"
            " WHERE maxrev IS NOT NULL AND ntotal IS NOT NULL AND bk IS NOT NULL"
            "   AND LEAST(100, bk / GREATEST(31 - COALESCE(bl,0), 1) * 100) >= %s",
            [MARKET_MIN_OCC]).fetchall()
        try:
            d = conn.execute("SELECT MAX(snapshot_date) FROM samsam_snapshots").fetchone()[0]
            out["asof"] = str(d)[:10] if d else ""
        except Exception:
            pass
        conn.close()
    except Exception:
        return out

    buckets = {}   # (지역, 유형) → [기대 월순수익…]
    for sido, btype, net in rows:
        if net is None:
            continue
        tname = next((t for t, codes in MARKET_TYPES.items() if btype in codes), None)
        if not tname:
            continue
        for rname, prefix in MARKET_REGIONS:
            if prefix is None or (sido or "").startswith(prefix):
                buckets.setdefault((rname, tname), []).append(float(net))

    regions = []
    for rname, _ in MARKET_REGIONS:
        keys = [(rname, t) for t in MARKET_TYPES]
        if not any(len(buckets.get(k, [])) >= MARKET_MIN_N for k in keys):
            continue                      # 어느 유형도 표본이 없으면 지역 선택지에서 제외
        regions.append(rname)
        for _, tname in keys:
            vals = sorted(buckets.get((rname, tname), []))
            if len(vals) < MARKET_MIN_N:
                continue
            bins = []
            for label, lo, hi in MARKET_BINS:
                n = sum(1 for x in vals
                        if (lo is None or x >= lo) and (hi is None or x < hi))
                bins.append({"label": label, "count": n, "min": lo, "max": hi})
            out["data"][f"{rname} · {tname}"] = {
                "bins": bins, "total": len(vals),
                "p25": round(_pct(vals, 0.25)), "median": round(_pct(vals, 0.5)),
                "p75": round(_pct(vals, 0.75))}
    out["regions"] = regions or ["전국"]
    _MARKET_CACHE.update(t=now, data=out)
    return out


LANDING = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>rendit · 단기임대 분석</title>
<meta property="og:type" content="website">
<meta property="og:title" content="rendit · 단기임대 수익성 분석">
<meta property="og:description" content="공급부족 스팟 파인더 · 지도 검색 · 수익 계산기 — 이 월세 매물, 단기임대로 돌리면 얼마 벌까?">
<meta property="og:image" content="https://rendits.duckdns.org/og.png">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta property="og:url" content="https://rendits.duckdns.org/">
<meta name="twitter:card" content="summary_large_image">

<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHZpZXdCb3g9JzAgMCAxMDAgMTAwJz48Y2lyY2xlIGN4PSc1MCcgY3k9JzUwJyByPSc1MCcgZmlsbD0nIzQzMjFGMycvPjxnIGZpbGw9J25vbmUnIHN0cm9rZT0nI2ZmZicgc3Ryb2tlLXdpZHRoPScxMycgc3Ryb2tlLWxpbmVjYXA9J3JvdW5kJyBzdHJva2UtbGluZWpvaW49J3JvdW5kJz48cGF0aCBkPSdNNDAgMzRWNjcnLz48cGF0aCBkPSdNNDAgNDdDNDMgMzkgNTIgMzYgNjEgNDAnLz48L2c+PC9zdmc+">

<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard-dynamic-subset.css">
<style>
*{box-sizing:border-box}body{margin:0;font-family:"Pretendard","Malgun Gothic",sans-serif;
background:linear-gradient(140deg,#0f172a,#1e293b);min-height:100vh;color:#e2e8f0}
.wrap{max-width:860px;margin:0 auto}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:8px}
h1{font-size:24px;font-weight:800;margin:0}
.who{font-size:13px;color:#94a3b8}.who a{color:#93c5fd;text-decoration:none;font-weight:700;margin-left:10px}
.sub{color:#94a3b8;font-size:13.5px;margin:6px 0 28px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
.card{display:block;background:#fff;color:#1f2937;border-radius:14px;padding:22px;text-decoration:none;
box-shadow:0 10px 30px rgba(0,0,0,.25);transition:.15s}
.card:hover{transform:translateY(-3px);box-shadow:0 16px 40px rgba(0,0,0,.35)}
.card .ic{font-size:30px}.card h2{font-size:17px;margin:10px 0 4px;font-weight:800}
.card p{font-size:12.5px;color:#64748b;margin:0;line-height:1.5}
.admin{margin-top:22px}.admin a{color:#fca5a5;text-decoration:none;font-weight:700;font-size:13px}
.online{font-size:13px;color:#94a3b8}.online b{color:#34d399;font-weight:800}
.churn{margin-top:26px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.09);
border-radius:14px;padding:18px 20px}
.churn-h{font-size:14px;font-weight:800;color:#e2e8f0;margin-bottom:14px}
.churn-h .churn-d{font-weight:600;color:#64748b;font-size:12px;margin-left:6px}
.churn-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.churn-cell{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:11px;
padding:14px;text-align:center}
.cc-region{font-size:13px;color:#cbd5e1;font-weight:700;margin-bottom:8px}
.cc-nums{font-size:19px;font-weight:800;letter-spacing:-.01em}
.cc-nums .up{color:#34d399}.cc-nums .dn{color:#f87171;margin-left:8px}
.cc-total{font-size:11.5px;color:#64748b;margin-top:6px}
.ins-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.ins-cell{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:11px;padding:12px 13px}
.ins-name{font-size:12.5px;color:#cbd5e1;font-weight:700}
.ins-occ{font-size:20px;font-weight:800;margin-top:5px}
.ins-occ.hi{color:#34d399}.ins-occ.mid{color:#fbbf24}.ins-occ.lo{color:#f87171}.ins-occ.na{color:#64748b;font-size:14px}
.ins-sub{font-size:11px;color:#64748b;margin-top:4px;line-height:1.5}
.spot-panel{border-color:rgba(251,146,60,.35);background:rgba(251,146,60,.05)}
.spot-cell{display:block;text-decoration:none;transition:.12s}
.spot-cell:hover{transform:translateY(-2px);border-color:rgba(251,146,60,.5)}
.spot-note{font-size:11px;color:#64748b;margin-top:10px}
.poi-ev{color:#fbbf24;font-size:10.5px;font-weight:700}
.cost-ev{color:#93c5fd;font-size:10.5px;font-weight:700}
@media(max-width:640px){h1{font-size:21px}.card{padding:18px}}
</style></head><body><div class=wrap>
<div class=top>
  <h1>{{user.name or user.username or user.email}}님, 환영합니다 👋</h1>
  {% if online is not none %}<div class=online>👥 현재 접속 <b id=online>{{online}}</b>명</div>{% endif %}
</div>
<p class=sub>부동산을 단기임대로 돌리면 얼마 버는지 · 아래에서 원하는 분석을 선택하세요</p>
<div class=grid>
  <a class=card href="/profit/"><div class=ic>{ICON_PROFIT}</div><h2>통합 수익성</h2>
    <p>렌트 단기임대 풀가동 시 부동산 월세 대비 최대수익·순수익, 동/역 순위</p></a>
  <a class=card href="/samsam/"><div class=ic>{ICON_RENT}</div><h2>렌트 분석</h2>
    <p>옵션별 예약률 영향, 건물 인기(월순수익), 지역 예약률 트렌드</p></a>
  <a class=card href="/gangnam/"><div class=ic>{ICON_ESTATE}</div><h2>부동산 매물</h2>
    <p>{REGION_TEXT} 부동산 매물 카드/상세 탐색</p></a>
</div>
{% if churn %}
<div class=churn>
  <div class=churn-h>🔄 렌트 매물 변동 <span class=churn-d>최근 크롤 {{churn.date}}</span></div>
  <div class=churn-grid>
    {% for c in churn.cells %}
    <div class=churn-cell>
      <div class=cc-region>{{c.region}}</div>
      <div class=cc-nums><span class=up>+{{'{:,}'.format(c.added)}}</span><span class=dn>-{{'{:,}'.format(c.removed)}}</span></div>
      <div class=cc-total>현재 {{'{:,}'.format(c.total)}}</div>
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}
{% if ins %}
{% macro occcls(o) %}{{ 'na' if o is none else ('hi' if o>=50 else ('mid' if o>=30 else 'lo')) }}{% endmacro %}
{% if ins.spots %}
<div class="churn spot-panel">
  <div class=churn-h>🔥 공급부족 스팟 <span class=churn-d>수요는 높은데(예약률 {{55}}%↑) 렌트 매물이 적은 동네 — 진입 기회</span></div>
  <div class=ins-grid>
    {% for s in ins.spots %}
    <a class="ins-cell spot-cell" href="/calc?dong={{s.dong}}{% if s.cost %}&rent={{s.cost.rent}}&dep={{s.cost.dep}}{% endif %}">
      <div class=ins-name>{{s.sigungu}} <b>{{s.dong}}</b></div>
      <div class="ins-occ {{occcls(s.occ)}}">{{s.occ}}%</div>
      <div class=ins-sub>렌트 {{s.n_rent}}개 vs 부동산 {{'{:,}'.format(s.n_naver)}}개
        {% if s.net is not none %}<br>월순수익 <b style="color:{{'#34d399' if s.net>=0 else '#f87171'}}">{{s.net}}만</b>{% if s.margin is not none %} · 이익률 {{s.margin}}%{% endif %}{% else %}<br><span style="color:#475569">수익성 매칭 없음</span>{% endif %}
        {% if s.cost %}<br><span class=cost-ev>진입 월세 {{s.cost.rent}}만/보증금 {{'{:,}'.format(s.cost.dep)}}만</span>{% endif %}
        {% if s.poi %}<br><span class=poi-ev>{% for p in s.poi %}{{ {'hospital':'🏥','university':'🎓','industrial':'🏭','academy':'📚','transport':'🚄','tour':'🗼'}.get(p.kind,'📍') }}{{p.name}} {{(p.dist_m/1000)|round(1)}}km {% endfor %}</span>{% endif %}</div>
    </a>
    {% endfor %}
  </div>
  <div class=spot-note>예약률=수요 · 렌트 매물수=경쟁(적을수록 기회) · 부동산 매물수=진입 가능한 월세 물건 · 카드 클릭 → 수익 계산기</div>
</div>
{% endif %}
{% if ins.unclaimed %}
<div class="churn spot-panel" style="border-color:rgba(56,189,248,.35);background:rgba(56,189,248,.05)">
  <div class=churn-h>🎯 미개척 지역 <span class=churn-d>월세는 빨리 나가는데(회전율↑) 렌트(단기임대)가 아직 없는 동네</span></div>
  <div class=ins-grid>
    {% for u in ins.unclaimed %}
    <a class="ins-cell spot-cell" href="/calc?dong={{u.dong}}{% if u.cost %}&rent={{u.cost.rent}}&dep={{u.cost.dep}}{% endif %}">
      <div class=ins-name>{{u.sigungu}} <b>{{u.dong}}</b></div>
      <div class="ins-occ hi">{{u.turnover}}%</div>
      <div class=ins-sub>월세 회전율(최근7일 신규 {{u.new7}}/재고 {{u.active}}) · 렌트 매물 {{u.n_samsam}}개뿐
        {% if u.cost %}<br><span class=cost-ev>진입 월세 {{u.cost.rent}}만/보증금 {{'{:,}'.format(u.cost.dep)}}만</span>{% endif %}
        {% if u.poi %}<br><span class=poi-ev>{% for p in u.poi %}{{ {'hospital':'🏥','university':'🎓','industrial':'🏭','academy':'📚','transport':'🚄','tour':'🗼'}.get(p.kind,'📍') }}{{p.name}} {{(p.dist_m/1000)|round(1)}}km {% endfor %}</span>{% endif %}</div>
    </a>
    {% endfor %}
  </div>
  <div class=spot-note>회전율=최근7일 신규등록÷활성매물(월세 수요 신호, 삼삼 데이터 없이 계산) · 렌트 매물 2개 이하만 · 아직 아무도 안 뛰어든 곳일 수 있음</div>
</div>
{% endif %}
{% if ins.newlistings and ins.newlistings.top %}
<div class="churn spot-panel" style="border-color:rgba(52,211,153,.35);background:rgba(52,211,153,.05)">
  <div class=churn-h>🆕 신규 매물 급증 동네 <span class=churn-d>어제 대비 삼삼엠투 신규 등록이 많은 동 (오늘 총 {{ins.newlistings.total_new}}건)</span></div>
  <div class=ins-grid>
    {% for nl in ins.newlistings.top[:12] %}
    <a class="ins-cell spot-cell" href="/calc?dong={{nl.dong}}">
      <div class=ins-name>{{nl.sigungu}} <b>{{nl.dong}}</b></div>
      <div class="ins-occ hi">+{{nl.n}}</div>
      <div class=ins-sub>신규 등록 매물</div>
    </a>
    {% endfor %}
  </div>
  <div class=spot-note>새로 등록된 단기임대 매물이 몰리는 동네 = 공급이 늘고 있는 곳(경쟁↑) 또는 뜨는 지역 신호 · 매일 갱신</div>
</div>
{% endif %}
<div class=churn>
  <div class=churn-h>🏭 반도체 공장 인근 렌트 예약률 <span class=churn-d>반경 3km · 최근 1달</span></div>
  <div class=ins-grid>
    {% for f in ins.factories %}
    <div class=ins-cell>
      <div class=ins-name>{{f.name}}</div>
      <div class="ins-occ {{occcls(f.occ)}}">{{'%.1f'|format(f.occ) ~ '%' if f.occ is not none else '매물없음'}}</div>
      <div class=ins-sub>렌트 매물 {{f.n}}건</div>
    </div>
    {% endfor %}
  </div>
</div>
<div class=churn>
  <div class=churn-h>🏙️ 1기 신도시 렌트 예약률 <span class=churn-d>중심 반경 1.5km · 인근 역</span></div>
  <div class=ins-grid>
    {% for t in ins.newtowns %}
    <div class=ins-cell>
      <div class=ins-name>{{t.name}}</div>
      <div class="ins-occ {{occcls(t.occ)}}">{{'%.1f'|format(t.occ) ~ '%' if t.occ is not none else '매물없음'}}</div>
      <div class=ins-sub>🚇 {{t.stations|join(' · ') if t.stations else '-'}}<br>렌트 매물 {{t.n}}건</div>
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}
{% if user.role == 'admin' %}<div class=admin><a href="/auth/crawl">📊 크롤링 현황</a>
  &nbsp;·&nbsp; <a href="/auth/members">👥 회원 관리 →</a></div>{% endif %}
</div>
<script>
// 로그인 직후 대시보드에서 수익성 첫 화면을 미리 받아 캐시에 저장 → 수익성 탭 클릭 시 즉시 표시.
// 키는 ProfitList의 기본 조회 path(sta: 접두)와 동일해야 함.
(function(){
  var q='occ_min=20&sort=expNet&dir=desc&page=1&size=40';
  var key='sta:api/profit?'+q;
  try{
    if(localStorage.getItem(key))return;   // 이미 있으면 스킵
    fetch('/profit/api/profit?'+q,{credentials:'same-origin'})
      .then(function(r){return r.ok?r.json():null})
      .then(function(d){ if(d&&!d.demo){ try{localStorage.setItem(key,JSON.stringify({t:Date.now(),data:d}))}catch(e){} } })
      .catch(function(){});
  }catch(e){}
})();
// 접속자수 실시간 갱신(관리자에게만 #online 요소가 렌더됨)
(function(){
  var el=document.getElementById('online'); if(!el)return;
  setInterval(function(){
    fetch('/auth/api/online',{credentials:'same-origin'})
      .then(function(r){return r.ok?r.json():null})
      .then(function(d){if(d&&d.online!=null)el.textContent=d.online})
      .catch(function(){});
  },15000);
})();
</script>
</body></html>"""


PUBLIC_LANDING = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>rendit · 단기임대 수익성 분석</title>
<meta property="og:type" content="website">
<meta property="og:title" content="rendit · 단기임대 수익성 분석">
<meta property="og:description" content="공급부족 스팟 파인더 · 지도 검색 · 수익 계산기 — 이 월세 매물, 단기임대로 돌리면 얼마 벌까?">
<meta property="og:image" content="https://rendits.duckdns.org/og.png">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta property="og:url" content="https://rendits.duckdns.org/">
<meta name="twitter:card" content="summary_large_image">

<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHZpZXdCb3g9JzAgMCAxMDAgMTAwJz48Y2lyY2xlIGN4PSc1MCcgY3k9JzUwJyByPSc1MCcgZmlsbD0nIzQzMjFGMycvPjxnIGZpbGw9J25vbmUnIHN0cm9rZT0nI2ZmZicgc3Ryb2tlLXdpZHRoPScxMycgc3Ryb2tlLWxpbmVjYXA9J3JvdW5kJyBzdHJva2UtbGluZWpvaW49J3JvdW5kJz48cGF0aCBkPSdNNDAgMzRWNjcnLz48cGF0aCBkPSdNNDAgNDdDNDMgMzkgNTIgMzYgNjEgNDAnLz48L2c+PC9zdmc+">
<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard-dynamic-subset.css">
<style>
:root{--accent:#4D2EE9;--ink:#141824;--sub:#565C6E;--mut:#6B7080;--line:#ECEEF2;--line2:#E5E8EF;--bg-soft:#F7F8FB}
*{box-sizing:border-box}
body{margin:0;font-family:"Pretendard","Malgun Gothic",sans-serif;background:#fff;color:var(--ink);line-height:1.55}
a{color:var(--accent);text-decoration:none}
img,svg{max-width:100%}

/* 헤더 */
.hd{position:sticky;top:0;z-index:30;background:rgba(255,255,255,.92);backdrop-filter:blur(12px);
border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;
padding:14px 40px;flex-wrap:wrap;gap:10px}
.brand{font-size:21px;font-weight:900;letter-spacing:-.02em;color:var(--accent)}
.hd-cta{display:flex;align-items:center;gap:16px}
.hd-cta .login{font-size:14px;font-weight:600;color:var(--mut)}
.hd-cta .signup{font-size:14px;font-weight:700;color:var(--accent);border:1px solid #D7DBE4;padding:8px 15px;border-radius:8px}
@media(max-width:720px){.hd{padding:12px 18px}}

/* 히어로 */
.hero{background:linear-gradient(180deg,#F4F6FB 0%,#FBFCFE 70%,#fff 100%);border-bottom:1px solid #EEF0F4}
.hero-in{max-width:1140px;margin:0 auto;padding:68px 40px;text-align:center}
.hero h1{font-size:40px;font-weight:900;line-height:1.18;letter-spacing:-.03em;margin:0 0 18px;white-space:pre-line}
.hero p{font-size:17px;color:var(--sub);max-width:600px;margin:0 auto 32px;white-space:pre-line}
.hero .btns{display:flex;gap:16px;justify-content:center;flex-wrap:wrap;align-items:center}
.btn{font-size:16px;font-weight:700;padding:16px 30px;border-radius:10px;display:inline-block}
.btn-primary{color:#fff;background:var(--accent);box-shadow:0 6px 16px -8px var(--accent)}
.hero .signup-link{font-size:15px;font-weight:600;color:var(--mut)}
.signup-row{margin-top:16px}
.free-note{margin-top:22px;font-size:13px;color:#8990A0}
@media(max-width:640px){.hero h1{font-size:29px}.hero-in{padding:44px 20px}}

/* 공통 섹션 헤딩 */
.kicker{font-size:13px;font-weight:700;margin-bottom:8px}
.sec-h{text-align:center;margin-bottom:40px}
.sec-h h2{font-size:28px;font-weight:900;letter-spacing:-.02em;margin:0 0 6px}
.sec-h p{font-size:16px;color:var(--mut);margin:0}
@media(max-width:640px){.sec-h h2{font-size:22px}}

/* 2 · 문제 */
.problems{max-width:1140px;margin:0 auto;padding:70px 40px 68px}
.problems .kicker{color:#C0392B;text-align:center}
.pgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}
.pbox{background:var(--bg-soft);border:1px solid var(--line);border-radius:16px;padding:26px}
.pbox .t{font-size:17px;font-weight:800;margin:14px 0 8px}
.pbox .d{font-size:14px;color:var(--mut)}

/* 3 · 서비스 3종 */
.services{max-width:1140px;margin:0 auto;padding:64px 40px 68px}
.services .kicker{color:var(--accent);text-align:center}
.sgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}
.scard{background:#fff;border:1px solid var(--line2);border-radius:18px;padding:28px;position:relative;
box-shadow:0 6px 20px -14px rgba(20,24,36,.25)}
.scard .badge{position:absolute;top:20px;right:20px;font-size:11px;font-weight:800;padding:4px 10px;border-radius:20px}
.badge-free{background:#E4F3EC;color:#0F9B62}
.badge-mem{background:#FDF0D9;color:#B7791F}
.scard .t{font-size:17px;font-weight:800;margin:10px 0 8px}
.scard .d{font-size:14px;color:var(--mut);margin-bottom:18px}
.scard .link{font-size:14px;font-weight:700}

/* 4 · 무료 계산기 */
.calc{max-width:1040px;margin:0 auto;padding:72px 40px 68px}
.calc-box{background:#fff;border:1px solid var(--line2);border-radius:22px;padding:38px;
box-shadow:0 30px 70px -34px rgba(20,24,36,.35)}
.calc-h{display:flex;align-items:center;gap:9px;margin-bottom:26px;flex-wrap:wrap}
.pill-green{background:#E4F3EC;color:#0F9B62;font-size:14px;font-weight:800;padding:4px 11px;border-radius:12px}
.calc-h .t{font-size:16px;font-weight:800}
.calc-cols{display:flex;gap:36px;flex-wrap:wrap}
.calc-inputs{flex:1 1 300px;min-width:280px;display:flex;flex-direction:column;justify-content:center;gap:26px}
.slider-row .lbl{display:flex;justify-content:space-between;font-size:14px;font-weight:700;margin-bottom:8px}
.slider-row .lbl span:last-child{color:var(--accent);font-weight:900}
.slider-row input[type=range]{width:100%;accent-color:var(--accent)}
.calc-out{flex:1 1 280px;min-width:260px;background:linear-gradient(160deg,#F1F4FC 0%,#EAF6F0 100%);
border:1px solid #E1E6F2;border-radius:16px;padding:28px;display:flex;flex-direction:column;
justify-content:center;text-align:center}
.calc-out .lbl{font-size:13px;color:var(--sub);margin-bottom:6px}
.calc-out .num{font-size:42px;font-weight:900;color:#0F9B62;letter-spacing:-.02em;line-height:1.1}
.calc-out .sub{font-size:13px;color:#7C8090;margin-top:8px}
.calc-out .sub b{color:#0F9B62}
.calc-out hr{border:none;height:1px;background:#DCE1EC;margin:20px 0;width:100%}
.calc-out .match{font-size:13px;color:var(--sub)}
.calc-out .match b{color:var(--ink)}
.nudge{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:24px;background:var(--ink);
border-radius:14px;padding:18px 22px}
.nudge p{flex:1 1 300px;font-size:14px;font-weight:600;color:#fff;margin:0}
.nudge a{font-size:14px;font-weight:800;color:var(--ink);background:#fff;padding:11px 20px;border-radius:9px;
white-space:nowrap}
@media(max-width:640px){.calc-box{padding:24px}}

/* 5 · 공급부족 스팟 (잠금) */
.wall{max-width:1140px;margin:0 auto;padding:64px 40px 20px}
.wall .sec-h span.tag2{background:var(--accent);color:#fff;font-size:12px;font-weight:800;padding:6px 12px;
border-radius:12px;display:inline-block;margin-bottom:16px}
.wall-panel{position:relative;border-radius:20px;overflow:hidden;border:1px solid var(--line2)}
.wall-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line2)}
.wcell{background:#fff;padding:52px 22px;min-height:210px;box-sizing:border-box}
.wcell.locked{filter:blur(3.5px);user-select:none}
.wcell .k{font-size:12px;font-weight:800;margin-bottom:8px}
.wcell .k.open{color:#0F9B62}
.wcell .k.lock{color:#B7791F}
.wcell .name{font-size:17px;font-weight:900;margin-bottom:4px}
.wcell .n{font-size:13px;color:var(--mut)}
.wall-overlay{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;
background:linear-gradient(180deg,rgba(255,255,255,0) 0%,rgba(255,255,255,.72) 55%,rgba(255,255,255,.96) 100%)}
.wall-card{background:#fff;border:1px solid var(--line2);border-radius:16px;padding:34px 26px;text-align:center;
box-shadow:0 20px 50px -24px rgba(20,24,36,.35);max-width:380px}
.wall-card .t{font-size:15px;font-weight:900;margin-bottom:6px}
.wall-card .d{font-size:13px;color:var(--mut);margin-bottom:16px}
.wall-card a{display:inline-block;font-size:14px;font-weight:800;color:#fff;background:var(--accent);
padding:11px 22px;border-radius:9px}
@media(max-width:720px){.wall-grid{grid-template-columns:1fr}.wcell{padding:32px 20px}}

/* 6 · 신뢰 */
.trust{background:var(--bg-soft);border-top:1px solid var(--line);border-bottom:1px solid var(--line);
margin-top:64px;padding:72px 40px}
.trust-in{max-width:1140px;margin:0 auto}
.trust .sec-h span.tag3{background:#E6F1FB;color:#185FA5;font-size:12px;font-weight:800;padding:4px 12px;
border-radius:12px;display:inline-block;margin-bottom:16px}
.tgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}
.tcard{background:#fff;border:1px solid var(--line);border-radius:16px;padding:26px}
.tcard .ic{font-size:22px;margin-bottom:12px}
.tcard .t{font-size:16px;font-weight:900;margin-bottom:6px}
.tcard .d{font-size:14px;color:var(--mut)}
.trust-note{text-align:center;font-size:13px;color:#8990A0;margin-top:28px}

/* 7 · 회원가입 CTA */
.signup-sec{max-width:1140px;margin:0 auto;padding:80px 40px}
.signup-card{background:#181430;border-radius:24px;padding:45px 40px;text-align:center}
.signup-card h2{font-size:28px;font-weight:900;color:#fff;margin:0 0 12px}
.signup-card p{font-size:16px;color:#A9A4C9;margin:0 auto 28px;max-width:600px}
.signup-card .btn{color:#fff;background:var(--accent);font-weight:900;padding:16px 36px;border-radius:11px;
transition:.15s}
.signup-card .btn:hover{color:var(--accent);background:#fff}

/* 푸터 */
.foot{padding:32px 40px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;
gap:12px;color:#8990A0;font-size:13px;border-top:1px solid var(--line)}
.foot .brand{font-size:21px}
</style></head><body>

<!-- 헤더 : 무료 도구가 1순위, 회원가입은 2순위 -->
<header class=hd>
  <div class=brand>rendit</div>
  <div class=hd-cta>
    <a class=login href="/auth/login">로그인</a>
    <a class=signup href="/auth/signup">회원가입</a>
  </div>
</header>

<!-- 1 · 히어로 -->
<section class=hero><div class=hero-in>
  <h1>단기임대로 돈 되는 매물,
어디서 찾아야 할까?</h1>
  <p>가입 없이 지도와 계산기부터 사용해보세요.
수요는 높은데 공급이 부족한 스팟과 수익이 나는 매물을 rendit이 골라드립니다.</p>
  <div class=btns>
    <a class="btn btn-primary" href="/calc">🧮 무료 계산기 써보기</a>
    <a class="btn btn-primary" href="/map">🗺️ 지도 미리보기</a>
  </div>
  <div class=signup-row><a class=signup-link href="/auth/signup">또는 회원가입 →</a></div>
  <div class=free-note>가입 없이 바로 — <b>계산기는 전체 무료</b>, <b>지도는 강남권 무료 미리보기</b></div>
</div></section>

<!-- 2 · 문제상황 -->
<section class=problems>
  <div class=sec-h>
    <div class=kicker>이런 고민, 있으셨죠?</div>
    <h2>매물은 많은데, 어디가 돈 되는지 모르시죠</h2>
  </div>
  <div class=pgrid>
    <div class=pbox>{ICON_LOST}<div class=t>어디부터 봐야 할지 막막</div>
      <div class=d>지역·예산 기준이 없어 매물 표만 뒤지다 시간을 보냅니다.</div></div>
    <div class=pbox>{ICON_SCATTER}<div class=t>데이터가 흩어져 있어요</div>
      <div class=d>매물 정보와 실제 예약 수요가 따로 놀아 비교가 어렵습니다.</div></div>
    <div class=pbox>{ICON_WARN}<div class=t>공급 과잉 지역이 걱정</div>
      <div class=d>이미 숙소가 넘치는 동네에 잘못 들어가면 예약이 안 들어옵니다.</div></div>
  </div>
</section>

<!-- 3 · 서비스 3종 -->
<section class=services>
  <div class=sec-h>
    <div class=kicker>rendit이 해결합니다</div>
    <h2>3가지 도구로 매물의 답을 봅니다</h2>
    <p>계산기와 지도는 지금 바로 무료로, 좋은 매물 후보를 보고 싶다면 가입하면 열립니다.</p>
  </div>
  <div class=sgrid>
    <div class=scard><span class="badge badge-free">무료</span>{ICON_CALC}
      <div class=t>수익 계산기</div>
      <div class=d>월세 대비 단기임대 전환 시 기대 순수익과 영업이익률을 즉시 계산.</div>
      <a class=link href="/calc">지금 계산하기 →</a></div>
    <div class=scard><span class="badge badge-free">무료</span>{ICON_MAP}
      <div class=t>지역 수익 지도</div>
      <div class=d>동·역별 평균 순수익과 예약률을 지도에서 색으로 비교(강남권 무료).</div>
      <a class=link href="/map">지도 열기 →</a></div>
    <div class=scard><span class="badge badge-mem">회원</span>{ICON_SPOT}
      <div class=t>공급부족 스팟 파인더</div>
      <div class=d>수요 대비 공급이 부족한 스팟을 점수화해 근처 매물까지 매칭.</div>
      <a class=link style="color:#B7791F" href="#signup">가입하고 열기 →</a></div>
  </div>
</section>

<!-- 4 · 무료 계산기 (실시간) -->
<section class=calc id=calc>
  <div class=calc-box>
    <div class=calc-h><span class=pill-green>무료 체험</span><span class=t>지금 슬라이더를 움직여 보세요</span></div>
    <div class=calc-cols>
      <div class=calc-inputs>
        <div class=slider-row>
          <div class=lbl><span>현재 월 임차료</span><span id=rentLabel>90만원</span></div>
          <input type=range id=i-rent min=50 max=300 step=10 value=90>
        </div>
        <div class=slider-row>
          <div class=lbl><span>주 임대료 (단기임대)</span><span id=weeklyLabel>45만원</span></div>
          <input type=range id=i-weekly min=20 max=90 step=5 value=45>
        </div>
        <div class=slider-row>
          <div class=lbl><span>예상 예약률</span><span id=occLabel>70%</span></div>
          <input type=range id=i-occ min=40 max=95 step=5 value=70>
        </div>
      </div>
      <div class=calc-out>
        <div class=lbl>단기임대 전환 시 기대 월순수익</div>
        <div class=num id=o-profit>+0만원</div>
        <div class=sub>월세 대비 <b id=o-uplift>-</b>배 · 영업이익률 <b id=o-margin>-</b></div>
        <hr>
        <div class=match>이 조건에 맞는 매물이<br>강남권에 <b id=o-match>-</b>개 있어요</div>
      </div>
    </div>
    <div class=nudge>
      <p>💡 매물마다 일일이 계산하기 귀찮다면? 수익 나는 <b>추천 스팟</b>을 rendit이 골라드려요.</p>
      <a href="/auth/signup">추천 스팟 보기 →</a>
    </div>
  </div>
</section>

<!-- 5 · 공급부족 스팟 파인더 (잠금 미리보기) -->
<section class=wall id=wall>
  <div class=sec-h>
    <span class=tag2>⭐ 공급부족 스팟 파인더</span>
    <h2>단기임대 수요는 높은데 공급이 없는 동네,<br>지도에서 바로 찾으세요</h2>
    <p>계산기·지도는 강남권까지 무료. {REGION_TEXT} 전 지역과 공급부족 스팟 + 근처 매물 매칭은 가입하면 열려요.</p>
  </div>
  <div class=wall-panel>
    <div class=wall-grid>
      <div class=wcell>
        <div class="k open">✓ 무료로 열림</div>
        <div class=name>강남권</div>
        <div class=n>평균 순수익 +164만원 · 매물 12개</div>
      </div>
      <div class="wcell locked">
        <div class=k>전 지역</div>
        <div class=name>마포·성동·해운대…</div>
        <div class=n>평균 순수익 +███만원 · 매물 ███개</div>
      </div>
      <div class="wcell locked">
        <div class="k lock">🔒 ⭐ 공급부족 스팟</div>
        <div class=name>██동 · 추천점수 ██점</div>
        <div class=n>순수익 +███만원 · 매칭 매물 ██개</div>
      </div>
    </div>
    <div class=wall-overlay>
      <div class=wall-card>
        <div class=t>🔓 전체 지도·추천 스팟 잠금 해제</div>
        <div class=d>{REGION_TEXT} 전 지역 순위와 공급부족 추천 스팟을 지도에서 한눈에.</div>
        <a href="/auth/signup">가입하고 전체 열기 →</a>
      </div>
    </div>
  </div>
</section>

<!-- 6 · 신뢰 -->
<section class=trust id=trust><div class=trust-in>
  <div class=sec-h>
    <span class=tag3>데이터 근거</span>
    <h2>"이 숫자에 내 돈 걸어도 되나?"</h2>
    <p>추정이 아니라 실제 데이터로 계산합니다.</p>
  </div>
  <div class=tgrid>
    <div class=tcard><div class=ic>🗂️</div><div class=t>데이터 출처</div>
      <div class=d>부동산 실매물을 실제 단기임대 예약 데이터와 매칭해 계산합니다.</div></div>
    <div class=tcard><div class=ic>🧮</div><div class=t>계산 근거·방법</div>
      <div class=d>주 임대료 × 예약률로 연 매출을 산출(÷52 환산), 연 임차원가를 빼 월 순수익을 계산.</div></div>
    <div class=tcard><div class=ic>🔄</div><div class=t>최신 갱신일</div>
      <div class=d>매물·예약 데이터를 주기적으로 갱신하고 화면마다 기준일을 표기합니다.</div></div>
  </div>
  <p class=trust-note>출처: 부동산 매물 및 단기임대 예약 데이터 기준</p>
</div></section>

<!-- 7 · 마지막 후킹 + 회원가입 -->
<section class=signup-sec id=signup>
  <div class=signup-card>
    <h2>계산해 봤다면, 이제 전체를 열어보세요</h2>
    <p>{REGION_TEXT} 전 지역 순위, 공급부족 추천 스팟 지도, 상세 수익 리포트까지 회원에게 열립니다.</p>
    <a class=btn href="/auth/signup">가입하고 전체 열기</a>
  </div>
</section>

<footer class=foot>
  <div class=brand>rendit</div>
  <div>© 2026 rendit. All rights reserved.</div>
</footer>

<script>
(function(){
  var rent=document.getElementById('i-rent'), weekly=document.getElementById('i-weekly'), occEl=document.getElementById('i-occ');
  function calc(){
    var r=+rent.value, w=+weekly.value, occ=+occEl.value;
    var weeklyRev=w*(occ/100), annualRev=weeklyRev*52, annualCost=r*12;
    var profit=Math.max(0, Math.round((annualRev-annualCost)/12));
    var margin=annualRev>0 ? Math.round((annualRev-annualCost)/annualRev*100) : 0;
    var uplift=(annualRev/12/r).toFixed(1);
    var match=8+Math.round((occ-40)/8);
    document.getElementById('rentLabel').textContent=r+'만원';
    document.getElementById('weeklyLabel').textContent=w+'만원';
    document.getElementById('occLabel').textContent=occ+'%';
    document.getElementById('o-profit').textContent='+'+profit+'만원';
    document.getElementById('o-uplift').textContent=uplift;
    document.getElementById('o-margin').textContent=margin+'%';
    document.getElementById('o-match').textContent=match;
  }
  [rent, weekly, occEl].forEach(function(el){ el.addEventListener('input', calc); });
  calc();
})();
</script>
</body></html>"""


# rendit 아이콘 타일(라운드 사각 + 흰 글리프, 로고의 둥근 기하학 톤). 다크/라이트 배경 공통.
# fill 기본값은 로그인 후 대시보드(LANDING)의 기존 브랜드색 유지 — 신규 브랜드색(#4D2EE9)은
# 공개 랜딩(PUBLIC_LANDING) 전용 아이콘에만 명시적으로 넘긴다.
def _tile(glyph, fill='#4321F3'):
    return (
        '<svg width="46" height="46" viewBox="0 0 46 46" fill="none" '
        'style="display:block">'
        f'<rect width="46" height="46" rx="13" fill="{fill}"/>'
        '<g stroke="#fff" stroke-width="2.5" stroke-linecap="round" '
        'stroke-linejoin="round">' + glyph + '</g></svg>'
    )
# 수익성: 우상향 라인 + 화살촉
ICON_PROFIT = _tile('<path d="M14 31l7-7 5 4 7-9"/><path d="M29 17h5v5"/>')
# 렌트 데이터: 예약 캘린더 + 체크
ICON_RENT = _tile('<rect x="14" y="16" width="18" height="16" rx="3"/>'
                  '<path d="M14 21h18M19 14v4M27 14v4M19 26l2.4 2.4L26 24"/>')
# 부동산 매물: 건물/집 + 문
ICON_ESTATE = _tile('<path d="M16 32V20l7-6 7 6v12"/><path d="M20 32v-6h6v6"/>')

_BRAND = '#4D2EE9'
# 계산기: 화면 + 버튼 그리드
ICON_CALC = _tile('<rect x="15" y="11" width="16" height="24" rx="3"/>'
                  '<path d="M18 16h10M18 22h2M22 22h2M26 22h2M18 27h2M22 27h2M26 27h2"/>', _BRAND)
# 지도: 접힌 지도 + 경계선
ICON_MAP = _tile('<path d="M14 18l7-3 8 3 7-3v19l-7 3-8-3-7 3z"/><path d="M21 15v19M29 18v19"/>', _BRAND)
# 공급부족 스팟: 별
ICON_SPOT = _tile('<path d="M23 13l3.5 7.2 7.9 1.1-5.7 5.6 1.3 7.9L23 31l-7 3.8 1.3-7.9-5.7-5.6 7.9-1.1z"/>', _BRAND)
# 문제1 - 막막함: 돋보기 + 물음표
ICON_LOST = _tile('<circle cx="19" cy="19" r="8"/><path d="M25 25l7 7"/>'
                  '<path d="M16.3 16.3c0-2 1.6-3.3 3-3.3 1.6 0 3 1 3 2.5 0 2.1-3 2.3-3 4.8"/>'
                  '<path d="M19.2 23.6v.2"/>', _BRAND)
# 문제2 - 흩어진 데이터: 연결 안 된 막대·점
ICON_SCATTER = _tile('<path d="M15 30v-8M23 30V13M31 30v-13"/>'
                     '<circle cx="15" cy="19" r="1.6"/><circle cx="23" cy="10" r="1.6"/>'
                     '<circle cx="31" cy="14" r="1.6"/>', _BRAND)
# 문제3 - 공급과잉 경고: 삼각 느낌표
ICON_WARN = _tile('<path d="M23 13l11 19H12z"/><path d="M23 20v6"/>'
                  '<circle cx="23" cy="29.3" r="0.8"/>', _BRAND)

_ICONS = {"ICON_PROFIT": ICON_PROFIT, "ICON_RENT": ICON_RENT, "ICON_ESTATE": ICON_ESTATE,
          "ICON_CALC": ICON_CALC, "ICON_MAP": ICON_MAP, "ICON_SPOT": ICON_SPOT,
          "ICON_LOST": ICON_LOST, "ICON_SCATTER": ICON_SCATTER, "ICON_WARN": ICON_WARN,
          # 서비스 커버리지 문구 — 지역이 늘면(부산·천안 등) 문구도 같이 바뀐다
          "REGION_TEXT": target_regions.display()}
for _k, _v in _ICONS.items():
    LANDING = LANDING.replace("{" + _k + "}", _v)
    PUBLIC_LANDING = PUBLIC_LANDING.replace("{" + _k + "}", _v)


# ── 수익 계산기(/calc) — 사용자의 엑셀('천사의 도시') 로직 이식 ──────────────────
# 연원가 주간화(÷52) + 소모품 → 주 임대원가, 주매출 대비 순익·손익주·영업이익률,
# 공실률 시나리오별 연/월 순수익. 전부 클라이언트 JS로 실시간 계산.
CALC_PAGE = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>rendit · 수익 계산기</title>
<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard-dynamic-subset.css">
<style>
*{box-sizing:border-box}
/* 포인트 색상(브랜드/수익/손실/골드)은 Figma 값 대신 사용자가 기존에 정의해둔 디자인
   토큰으로 — 배경·텍스트·보더 등 구조색은 Figma Make export 값 유지 */
:root{--brand:#4D2EE9;--brand-hover:#3A1FC9;--brand-tint:#ECEAF8;--profit:#148A5E;--loss:#D24545;
--profit-bg:#EAF6F0;--loss-bg:#FBEAEA;--gold-bg:#FDF8ED;
--bg:#E7E9F3;--text:#1B1B3A;--text-sub:#8080A8;--line:#E2E2F0;--gold:#D89700;--field-bg:#EFEFF9}
body{margin:0;font-family:"Pretendard","Malgun Gothic",sans-serif;
background:var(--bg);min-height:100vh;color:var(--text);padding:20px 14px 60px}
.hd{position:sticky;top:0;z-index:30;background:rgba(255,255,255,.92);backdrop-filter:blur(12px);
border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;
padding:14px 40px;margin:-20px -14px 20px}
.hd .brand{font-size:21px;font-weight:900;letter-spacing:-.02em;color:var(--brand);text-decoration:none}
.hd-cta{display:flex;align-items:center;gap:16px}
.hd-cta .login{font-size:14px;font-weight:600;color:var(--text-sub);text-decoration:none}
.hd-cta .signup{font-size:14px;font-weight:700;color:var(--brand);border:1px solid var(--line);
padding:8px 15px;border-radius:8px;text-decoration:none}
/* 로그인 시 auth._inject_nav가 body 시작 직후 삽입하는 공통 네비바(#__nav)도
   body의 padding에 밀리지 않게 동일하게 상쇄 — 다른 뷰어(body padding:0)와 간격 맞춤.
   주의: _inject_nav는 여는 태그 문자열을 앞에서부터 찾아 그 바로 뒤에 네비바를 끼워넣는다.
   그 탐색 문자열을 이 style 블록의 주석 등에 예시로 그대로 적으면, 실제 태그보다 먼저
   매치돼 네비바가 문서 앞쪽 엉뚱한 자리에 삽입되는 버그가 난다(실제로 겪음) — 그래서
   이 주석에서도 그 문자열 형태를 그대로 쓰지 않는다. */
#__nav{margin:-20px -14px 20px}
.wrap{max-width:1120px;margin:0 auto}
.back{display:inline-block;margin-bottom:14px;color:var(--text-sub);text-decoration:none;font-size:13px;font-weight:700}
.back:hover{color:var(--brand)}
.ic{display:inline-flex;vertical-align:-0.15em;margin-right:6px}
.ic svg{width:1em;height:1em;display:block}
.ic-brand{color:var(--brand)}
h1{font-size:22px;font-weight:900;margin:4px 0 2px;letter-spacing:-.01em}
.sub{color:var(--text-sub);font-size:13px;margin:0;line-height:1.6}
.sec-label{font-size:17px;font-weight:700;color:var(--text-sub);margin:0 0 9px}
/* 상단 — 왼쪽 제목/설명, 오른쪽 매물유형 탭 + 초기화 (좁은 화면에서 아래로 줄바꿈).
   원본은 header 밑에 별도 흰 스트립(풀블리드)이지만, 이 프로젝트는 body padding 기반이라
   카드 형태로 근사(리스크가 큰 풀블리드 마진 트릭은 .hd 하나로 충분). */
/* App.tsx는 sm:items-end(하단 정렬) — 타이틀이 2줄이라 우측 컨트롤이 위쪽에 붕 뜨는 것보다
   아래쪽에 맞춰야 자연스럽다는 원본 그대로 반영 */
.top-bar{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;
flex-wrap:wrap;background:#fff;border-radius:16px;
box-shadow:0 1px 2px rgba(27,27,58,.04),0 14px 28px -16px rgba(27,27,58,.15);
padding:22px 24px;margin-bottom:32px}
.top-controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.type-tabs{display:flex;align-items:center;gap:6px}
.type-label{font-size:12px;color:var(--text-sub);margin-right:4px}
.tab{border:none;background:var(--field-bg);color:var(--text-sub);font-size:12.5px;
font-weight:600;padding:7px 14px;border-radius:999px;cursor:pointer;font-family:inherit;
white-space:nowrap;transition:background .15s,color .15s}
.tab:hover{color:var(--text)}
.tab.active{background:var(--brand);color:#fff}
.reset-btn{border:none;background:var(--field-bg);color:var(--text-sub);font-size:12.5px;
font-weight:600;padding:7px 14px;border-radius:999px;cursor:pointer;font-family:inherit;
white-space:nowrap;display:inline-flex;align-items:center;gap:4px}
.reset-btn:hover{color:var(--text);background:#E2E2F0}
/* 좌(결과)/우(입력) 2단 — align-items:start로 카드가 서로 키를 맞추지 않게(빈 공간 방지).
   DOM은 입력이 먼저지만(모바일에서 입력→결과 순서가 자연스러움) 데스크톱에선 order로 자리를
   바꾼다: 결과가 왼쪽(1fr), 입력이 오른쪽(410px) — 폭은 그대로. */
.layout{display:grid;grid-template-columns:1fr 410px;gap:16px;margin-bottom:32px;align-items:start}
.left-col,.right-col{display:flex;flex-direction:column;gap:16px}
.left-col{order:2}
.right-col{order:1}
@media(max-width:900px){.layout{grid-template-columns:1fr}.left-col{order:1}.right-col{order:2}}
.box{background:#fff;border-radius:16px;padding:20px;
box-shadow:0 1px 2px rgba(27,27,58,.04),0 14px 28px -16px rgba(27,27,58,.15)}
/* card-head를 box 패딩 밖으로 블리드시켜 하단 보더로 구분된 별도 스트립처럼 보이게 */
.card-head{display:flex;align-items:baseline;justify-content:space-between;
margin:-20px -20px 16px;padding:16px 20px;border-bottom:1px solid var(--line)}
.card-head h2{font-size:14px;font-weight:700;margin:0;color:var(--text)}
.card-head .unit-badge{font-size:11px;font-weight:700;color:var(--text-sub)}
.hint{font-size:12px;color:var(--text-sub);margin:0 0 16px}
.field-primary{margin:14px 0}
.field-primary label{display:block;font-size:13px;color:var(--text-sub);margin-bottom:6px}
.field-box{display:flex;align-items:center;justify-content:flex-end;gap:6px;border:1px solid transparent;
border-radius:12px;padding:10px 14px;background:var(--field-bg)}
.field-box:focus-within{border-color:var(--brand);background:#fff;box-shadow:0 0 0 3px var(--brand-tint)}
.field-box input{border:none;outline:none;background:transparent;font-size:15px;font-weight:600;
color:var(--text);text-align:right;width:100%;font-family:inherit}
.field-box .unit{font-size:12px;color:var(--text-sub);font-weight:500;white-space:nowrap}
/* 접기(details) 없이 항상 펼친 상태 — 클릭해야 보이는 게 불친절하다는 피드백(장효령, 08-06) */
.more{margin-top:16px;border-top:1px solid var(--line);padding-top:14px}
.more-title{font-size:14px;font-weight:700;color:var(--text);line-height:1;margin:0 0 2px}
/* 공실률 설정 카드 헤더 — 좌: 라벨+타이틀, 우: 큰 일수 숫자 */
.vac-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:20px}
.vac-eyebrow{font-size:12px;color:var(--text-sub);margin:0 0 2px}
.vac-head h2{font-size:14px;font-weight:700;margin:0}
.vac-num{text-align:right}
.vac-num b{display:block;font-size:28px;font-weight:700;color:var(--brand);line-height:1}
.vac-num span{display:block;font-size:11px;color:var(--text-sub);margin-top:2px}
.vac-range{width:100%;accent-color:var(--brand)}
.scale{display:flex;justify-content:space-between;font-size:11px;color:var(--text-sub);margin-top:6px}
.vac-insight{display:flex;align-items:center;gap:8px;margin-top:16px;padding:10px 14px;
border-radius:12px;font-size:12.5px;font-weight:600}
.vac-insight.safe{background:var(--profit-bg);color:var(--profit)}
.vac-insight.unsafe{background:var(--loss-bg);color:var(--loss)}
.vac-insight svg{flex:none}
.pos{color:var(--profit)}.pos-brand{color:var(--brand)}.neg{color:var(--loss)}
.vac{margin-top:0}
.vac h2{margin-top:0;display:flex;align-items:center;font-size:20px}
.vac h2 .ic{margin-right:7px}
.vac-scroll{overflow-x:auto}
.vac table{width:100%;min-width:280px;border-collapse:collapse;font-size:12.5px}
.vac th,.vac td{padding:7px 6px;text-align:right;border-bottom:1px solid var(--line)}
.vac th{color:var(--text-sub);font-weight:700}.vac td:first-child,.vac th:first-child{text-align:left}
.vac tbody tr{cursor:pointer;transition:background .1s}
.vac tbody tr:hover,.vac tbody tr.active{background:var(--field-bg)}
.vac tbody tr.active td:first-child{color:var(--brand);font-weight:700}
.hero-card{padding:24px 22px}
.hero-sub{font-size:12px;color:var(--text-sub);margin:0 0 8px}
.hero-row{display:flex;flex-direction:column;align-items:flex-start;gap:8px}
/* line-height 명시 필수 — 유니코드 마이너스(−)가 Pretendard에 없어 폴백 폰트로
   렌더되면서 그 줄만 줄높이가 커져 카드가 세로로 늘어나는 버그가 있었음.
   ASCII 하이픈으로 통일했지만 향후 재발 방지 차원에서도 고정해둔다. */
.hero-num{font-size:36px;line-height:1.15;font-weight:700;letter-spacing:-.02em;white-space:nowrap}
.hero-annual{font-size:12px;line-height:1.4;color:var(--text-sub);white-space:nowrap;font-variant-numeric:tabular-nums}
@media(max-width:480px){.hero-num{font-size:27px}}
/* 매출에서 빠지는 선택 항목(플랫폼 수수료·부가세) — 체크 꺼짐이면 % 입력도 잠근다 */
.opts{margin-top:20px;border-top:1px solid var(--line);padding-top:14px}
.opts-title{font-size:13px;font-weight:700;color:var(--text);margin:0 0 10px}
.opts-sub{font-weight:600;color:var(--text-sub)}
.opt-row{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px}
.opt{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text);cursor:pointer}
.opt input[type=checkbox]{width:16px;height:16px;accent-color:var(--brand);cursor:pointer}
.opt-val{display:flex;align-items:center;gap:5px;border:1.5px solid var(--line);border-radius:9px;
padding:6px 10px;background:#fff}
.opt-val input{border:none;outline:none;background:transparent;width:52px;text-align:right;
font-size:14px;font-weight:700;color:var(--text);font-family:inherit}
.opt-val:focus-within{border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-tint)}
.opt-val.off{opacity:.45}
.opt-val .unit{font-size:12px;color:var(--text-sub);font-weight:600}
.opts-hint{font-size:11.5px;color:var(--text-sub);margin:8px 0 0;line-height:1.5}
/* '받는 돈' 카드 안 주 단위 요약 — 입력하는 자리에서 바로 결과가 보이게(카드 여백도 채움) */
.week-mini{margin-top:20px;border-top:1px solid var(--line);padding-top:14px;display:grid;gap:9px}
.week-mini>div{display:flex;justify-content:space-between;align-items:baseline;font-size:13px}
.week-mini span{color:var(--text-sub)}
.week-mini b{font-weight:700;color:var(--text);font-variant-numeric:tabular-nums;white-space:nowrap}
.week-mini .hl{margin-top:3px;padding-top:11px;border-top:1px dashed var(--line)}
.week-mini .hl span{color:var(--text);font-weight:700}
.week-mini .hl b{font-size:17px}
/* 손익분기 슬라이더 — App.tsx 그대로: 초록 반투명 fill=안전구간(0~손익분기일),
   손잡이=지금 선택한 공실일수 위치(안전이면 브랜드색, 아니면 손실색) */
.gauge-caption{font-size:13px;font-weight:700;margin:2px 0 14px}
.gauge2-track{position:relative;height:10px;background:var(--field-bg);border-radius:999px}
.gauge2-safe{position:absolute;inset:0 auto 0 0;top:0;bottom:0;left:0;background:var(--profit);
opacity:.15;border-radius:999px;transition:width .15s}
.gauge2-thumb{position:absolute;top:50%;transform:translateY(-50%);width:16px;height:16px;
border-radius:999px;background:var(--brand);border:2px solid #fff;
box-shadow:0 1px 4px rgba(27,27,58,.25);transition:left .15s,background .15s}
.gauge2-thumb.unsafe{background:var(--loss)}
.gauge2-cap{display:flex;justify-content:space-between;font-size:11px;font-weight:700;margin-top:10px}
/* 공실률별 시나리오 막대그래프 — 클릭하면 공실일수 슬라이더 값이 그 날짜로 바뀐다 */
.scenario-title{font-size:14px;font-weight:700;margin:2px 0 16px}
.scenario-chart{display:flex;align-items:flex-end;gap:6px;height:96px;margin-bottom:16px}
.sbar-col{flex:1;display:flex;flex-direction:column;align-items:center;height:100%;cursor:pointer}
.sbar-track{flex:1;display:flex;align-items:flex-end;width:100%}
.sbar{width:100%;border-radius:4px 4px 0 0;transition:opacity .15s}
.sbar-label{font-size:10px;color:var(--text-sub);margin-top:6px}
.sbar-col.active .sbar-label{color:var(--brand);font-weight:700}
/* 비슷한 매물과 비교하면 — 지역 드롭다운 + 목업 분포 히스토그램(정적 데이터) */
.market-card{margin-top:0}
.market-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;
margin-bottom:20px;flex-wrap:wrap}
.market-title{font-size:15px;font-weight:700;margin:0}
/* 네이티브 select는 펼친 옵션 목록을 OS가 직접 그려서 CSS로 스타일링이 안 되므로
   버튼+직접 그린 목록으로 만든 커스텀 드롭다운으로 교체 */
.market-region-wrap{position:relative}
.market-region{font-size:12px;border:none;background:var(--field-bg);color:var(--text);
border-radius:10px;height:34px;padding:0 32px 0 12px;min-width:120px;cursor:pointer;font-family:inherit;
display:inline-flex;align-items:center;position:relative}
.market-region svg{position:absolute;right:10px;top:50%;transform:translateY(-50%);
color:var(--text-sub);transition:transform .15s}
.market-region.open svg{transform:translateY(-50%) rotate(180deg)}
.market-region-menu{display:none;position:absolute;top:calc(100% + 6px);right:0;min-width:140px;
background:#fff;border-radius:12px;box-shadow:0 12px 32px -8px rgba(27,27,58,.25);
padding:6px;z-index:30}
.market-region-menu.show{display:block}
.market-region-opt{display:block;width:100%;text-align:left;padding:8px 12px;font-size:12.5px;
border-radius:8px;cursor:pointer;background:none;border:none;font-family:inherit;
color:var(--text);white-space:nowrap}
.market-region-opt:hover{background:var(--field-bg)}
.market-region-opt.active{background:var(--brand);color:#fff;font-weight:700}
.market-highlight{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;
background:var(--field-bg);border-radius:12px;margin-bottom:20px}
.market-mine{font-size:20px;font-weight:700;color:var(--brand);margin:0;font-variant-numeric:tabular-nums}
/* 옅은 틴트 뱃지는 하이라이트 박스(연보라 배경) 위에서 거의 안 보였음 —
   토스류 상태칩처럼 색을 꽉 채운 solid fill + 흰 글자로 위계를 확실히 줌 */
.market-badge{display:inline-flex;align-items:center;gap:4px;font-size:12.5px;font-weight:800;
background:var(--brand);color:#fff;padding:6px 12px;border-radius:999px}
.market-badge.neg{background:var(--loss)}
.market-badge svg{flex:none}
.market-chart{display:flex;align-items:flex-end;gap:5px;height:120px;margin-bottom:16px}
.mbar-col{flex:1;display:flex;flex-direction:column;align-items:center;height:100%}
.mbar-track{flex:1;display:flex;align-items:flex-end;width:100%}
/* min-height: 건수가 0에 가까운 구간도 막대가 보이게(내 구간이 하필 거기면 강조가 사라진다) */
.mbar{width:100%;min-height:4px;border-radius:3px 3px 0 0;background:var(--line);
transition:background .15s}
.mbar.user{background:var(--brand)}
.mbar-label{font-size:9.5px;color:var(--text-sub);margin-top:6px;text-align:center;line-height:1.3}
.market-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:16px;
padding-top:16px;border-top:1px solid var(--line)}
.market-stat-v{font-size:16px;font-weight:700}
.market-stat-v span{font-size:12px;color:var(--text-sub);font-weight:500;margin-left:2px}
.market-foot{font-size:11px;color:var(--text-sub);margin:12px 0 0;line-height:1.6}
.market-foot span.sep{margin:0 6px;color:var(--line)}
.kpi3{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:0}
@media(max-width:480px){.kpi3{grid-template-columns:1fr}}
.kpi3-cell{background:var(--field-bg);border-radius:16px;padding:16px 18px}
.kpi3-cell.warn{background:var(--gold-bg)}
.kpi3-cell.neg{background:var(--loss-bg)}
.kpi3-cell.posbg{background:var(--profit-bg)}
.kpi3-cell .l{font-size:12.5px;color:var(--text-sub);margin-bottom:6px}
.kpi3-cell.warn .l{color:var(--gold);font-weight:700}
.kpi3-cell .v{font-size:20px;font-weight:700;white-space:nowrap}   /* '+145,385 / 원' 줄바꿈 방지 */
.kpi3-cell .v.warn{color:var(--gold)}.kpi3-cell .v.neg{color:var(--loss)}.kpi3-cell .v.pos{color:var(--profit)}
.kpi3-paren{font-size:13px;font-weight:600;color:var(--text-sub)}
.kpi3-sub{font-size:11.5px;color:var(--text-sub);margin-top:4px}
/* 박스는 아이콘(13x13) 크기 그대로 — vertical-align:middle로 라벨 글자와 정확히
   가운데 정렬. 히트 영역 확장은 박스 크기에 전혀 영향 없는 ::before 유령 레이어로
   처리(이전엔 padding/margin으로 박스 자체를 키우다가 정렬이 계속 어긋났음) */
.info-wrap{position:relative;display:inline-flex;align-items:center;vertical-align:middle;margin-left:6px}
.info-wrap::before{content:'';position:absolute;inset:-6px;cursor:help}
.info-ic{width:13px;height:13px;display:flex;align-items:center;justify-content:center;
cursor:help;border-radius:50%}
.info-ic svg{width:13px;height:13px;display:block}
/* culc_redesign(App.tsx Hint 컴포넌트) 그대로: 어두운 카드, 200px 폭에서 줄바꿈, 여유 있는 패딩 */
.info-tip{display:none;position:absolute;top:calc(100% + 8px);left:50%;transform:translateX(-50%);
width:max-content;max-width:200px;background:rgba(27,27,58,.95);color:#F2F3FA;
font-size:12px;font-weight:500;line-height:1.6;padding:10px 12px;border-radius:12px;
white-space:normal;text-align:left;z-index:20;box-shadow:0 12px 32px rgba(0,0,0,.25)}
/* 짧은 문구는 굳이 200px에서 꺾이지 않고 한 줄로 — 내용 길이에 맞춰 폭이 늘어난다 */
.info-tip.nowrap{white-space:nowrap;max-width:none}
.info-wrap:hover .info-tip,.info-wrap.show .info-tip{display:block}
.be-row td{border-top:1px dashed var(--gold);border-bottom:none;color:var(--gold);font-size:11px;
font-weight:700;text-align:center;padding:6px 4px}
.cta-row{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;
margin-top:32px;padding:24px;background:#3d25b5;border-radius:16px;
box-shadow:0 14px 28px -14px rgba(77,46,233,.25)}
.cta-h1{font-size:15px;font-weight:700;color:#fff;margin:0 0 4px;line-height:1.4}
.cta-h2{font-size:13px;color:rgba(255,255,255,.7);margin:0;line-height:1.5}
.cta-btn{flex:0 0 auto;display:inline-flex;align-items:center;gap:6px;white-space:nowrap;
background:#fff;color:var(--brand);font-weight:700;font-size:13.5px;
padding:11px 20px;border-radius:12px;text-decoration:none}
.cta-btn:hover{opacity:.9}
/* 카드 헤더 옆 요약 배지 — "합계 94만원" / "주 순수익 35만원" */
.sum-badge{font-size:12px;color:var(--text-sub);display:flex;align-items:baseline;gap:6px}
.sum-badge b{font-size:15px;font-weight:700;color:var(--text)}
/* 입력 카드 내부 2열 필드 그리드(핵심/세부 구분 없이 전부 같은 무게) */
.field-grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}
.field-grid2:first-of-type{margin-top:16px}
@media(max-width:480px){.field-grid2{grid-template-columns:1fr}}
.field label{display:block;font-size:12.5px;color:var(--text-sub);margin-bottom:6px}
.field-full{margin-top:14px}
.field-full label{display:block;font-size:12.5px;color:var(--text-sub);margin-bottom:6px}
/* 수수료·부가세 — 체크박스+% 입력을 field-grid2 셀 하나에 맞춤 */
.field-opt label.opt{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--text-sub);
margin-bottom:6px;cursor:pointer}
.field-opt .opt input[type=checkbox]{width:14px;height:14px;accent-color:var(--brand);cursor:pointer}
.host-fee-note{font-size:12px;color:var(--text-sub);line-height:1.6;margin:2px 4px 0}
</style></head><body>
{% if not user %}
<header class=hd>
  <a class=brand href="/">rendit</a>
  <div class=hd-cta>
    <a class=login href="/auth/login">로그인</a>
    <a class=signup href="/auth/signup">회원가입</a>
  </div>
</header>
{% endif %}
<div class=wrap>
<a class=back href="/">← 대시보드</a>
<div class=top-bar>
  <div class=top-copy>
    <h1>단기임대 수익 계산기{% if dong %} — {{dong}}{% endif %}</h1>
    <p class=sub>내 매물로 한 달에 얼마 버는지 바로 확인해보세요.<br>원룸 기준으로 미리 채워져 있어요. 월 임대료, 보증금, 주당 숙박료만 내 매물에 맞게 바꾸면 됩니다.{% if rent %} <b style="color:var(--brand)">{{dong}} 시세(월세 {{rent}}만·보증금 {{dep}}만)를 자동 입력했어요.</b>{% endif %}</p>
  </div>
  <div class=top-controls>
    <div class=type-tabs>
      <span class=type-label>매물 유형</span>
      <button type=button class="tab active" data-type="원룸">원룸</button>
      <button type=button class=tab data-type="빌라·주택">빌라·주택</button>
      <button type=button class=tab data-type="오피스텔">오피스텔</button>
    </div>
    <button type=button class=reset-btn id=btn_reset><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg">
<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>
</svg>초기화</button>
  </div>
</div>
<div class=layout>
  <div class=left-col>
  <div class=box>
    <div class=card-head><h2>월 고정 지출</h2><span class=sum-badge>합계<b id=o_fixedsum>-</b></span></div>
    <div class=field-grid2>
      <div class=field><label>월 임대료<span class=info-wrap><span class=info-ic tabindex=0><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg></span><span class=info-tip>매월 임대인에게 지급하는 월세</span></span></label>
        <div class=field-box><input id=i_rent type=number value={{rent or 60}}><span class=unit>만원</span></div></div>
      <div class=field><label>보증금<span class=info-wrap><span class=info-ic tabindex=0><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg></span><span class=info-tip>계약 시 납부하는 보증금. 연 이자율 기준 기회비용이 월 지출에 포함됩니다.</span></span></label>
        <div class=field-box><input id=i_dep type=number value={{dep or 500}}><span class=unit>만원</span></div></div>
    </div>
    <div class=field-full><label>월 관리비<span class=info-wrap><span class=info-ic tabindex=0><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg></span><span class=info-tip>건물 관리비, 공용 비용 포함</span></span></label>
      <div class=field-box><input id=i_mgmt type=number value=150000 step=10000><span class=unit>원</span></div></div>
    <div class=field-grid2>
      <div class=field><label>보증금 이자율<span class=info-wrap><span class=info-ic tabindex=0><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg></span><span class=info-tip>연 기회비용율. 시중 예금금리 참고 (예: 3.5%)</span></span></label>
        <div class=field-box><input id=i_deprate type=number value=3.5 step=0.1><span class=unit>%/연</span></div></div>
      <div class=field><label>통신비<span class=info-wrap><span class=info-ic tabindex=0><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg></span><span class=info-tip>인터넷, TV 등 월정액 비용</span></span></label>
        <div class=field-box><input id=i_net type=number value=20000 step=1000><span class=unit>원</span></div></div>
    </div>
    <div class=field-grid2>
      <div class=field><label>청소 소모품(주당)<span class=info-wrap><span class=info-ic tabindex=0><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg></span><span class=info-tip>세제, 봉투 등 주간 소모품비</span></span></label>
        <div class=field-box><input id=i_clean type=number value=15000 step=1000><span class=unit>원</span></div></div>
      <div class=field><label>렌탈 용품(주당)<span class=info-wrap><span class=info-ic tabindex=0><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg></span><span class=info-tip>침구, 수건 등 주간 렌탈비</span></span></label>
        <div class=field-box><input id=i_supply type=number value=20000 step=1000><span class=unit>원</span></div></div>
    </div>
  </div>
  <div class=box>
    <div class=card-head><h2>예상 수익</h2><span class=sum-badge>주 순수익<b id=o_wnetbadge>-</b></span></div>
    <div class=field-grid2>
      <div class=field><label>주간 임대료<span class=info-wrap><span class=info-ic tabindex=0><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg></span><span class=info-tip>1주 기준 게스트에게 받는 임대료</span></span></label>
        <div class=field-box><input id=i_wrent type=number value=30><span class=unit>만원</span></div></div>
      <div class=field><label>청소·관리비<span class=info-wrap><span class=info-ic tabindex=0><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg></span><span class="info-tip nowrap">퇴실 시 게스트에게 별도 청구하는 청소비</span></span></label>
        <div class=field-box><input id=i_wmgmt type=number value=6><span class=unit>만원</span></div></div>
    </div>
    <div class=field-grid2>
      <div class=field><label>플랫폼 수수료<span class=info-wrap><span class=info-ic tabindex=0><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg></span><span class=info-tip>삼삼엠투 등 플랫폼 수수료율<br>(총 매출 기준)</span></span></label>
        <div class=field-box><input id=i_fee type=number value=3.3 step=0.1 min=0><span class=unit>%</span></div></div>
      <div class=field><label>부가가치세<span class=info-wrap><span class=info-ic tabindex=0><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg></span><span class=info-tip>일반과세자 10%, 간이과세자 0%</span></span></label>
        <div class=field-box><input id=i_vat type=number value=0 step=0.1 min=0><span class=unit>%</span></div></div>
    </div>
    <div class=week-mini>
      <div><span>주간 총 매출</span><b id=o_wrev_m>-</b></div>
      <div><span>수수료 + 부가세</span><b id=o_ded_m class=neg>-</b></div>
      <div class=hl><span>주간 순수익</span><b id=o_wnet_m>-</b></div>
    </div>
  </div>
  <p class=host-fee-note>삼삼엠투 호스트 수수료는 총 이용요금의 3.3%(VAT 포함)입니다. 처음 3개월은 공실률 25~35%로 보수적으로 계산해보길 권장해요.</p>
  </div>
  <div class=right-col>
  <div class=box>
    <div class=vac-head>
      <div><p class=vac-eyebrow>한 달에 며칠이나 예약될까요?</p><h2>예약일수 설정</h2></div>
      <div class=vac-num><b id=o_vacpct>0일 예약</b><span id=o_vacsub>31일 공실</span></div>
    </div>
    <input type=range id=i_vacancy min=0 max=31 step=1 value=28 class=vac-range>
    <div class=scale><span>0일 (한 달 공실)</span><span>31일 (풀예약)</span></div>
    <div class=vac-insight id=o_vinsight>-</div>
  </div>
  <div class="box hero-card">
  <p class=hero-sub>월 순수익</p>
  <div class=hero-row>
    <span class=hero-num id=o_heromonth>-</span>
    <span class=hero-annual id=o_heroyear>-</span>
  </div>
  </div>
  <div class=kpi3>
  <div class="kpi3-cell warn" id=o_kpi_be>
    <div class=l>손익분기</div>
    <div class=v id=o_bevalue>월 -일 예약</div>
    <div class=kpi3-sub id=o_besub>최대 -일 공실 허용</div>
  </div>
  <div class="kpi3-cell warn" id=o_kpi_margin>
    <div class=l>영업이익률</div>
    <div class=v id=o_margin>-</div>
    <div class=kpi3-sub id=o_marginsub>공실 -일 기준</div>
  </div>
</div>
<div class="box gauge-card">
  <p class=vac-eyebrow>언제부터 돈을 벌까</p>
  <p class=gauge-caption>월 <b id=o_gbeflag class=pos-brand>-</b>일 이하 공실이면 흑자</p>
  <div class=gauge2-track>
    <div class=gauge2-safe id=o_gsafe style="width:0%"></div>
    <div class=gauge2-thumb id=o_gthumb style="left:0%"></div>
  </div>
  <div class=gauge2-cap>
    <span class=pos>← 흑자 구간</span>
    <span class=neg>적자 구간 →</span>
  </div>
</div>
<div class="box vac">
  <p class=vac-eyebrow>공실률별로 보면<span class=info-wrap><span class=info-ic tabindex=0><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#4D2EE9" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg">
<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>
</svg></span><span class="info-tip nowrap">월 완전가동 순수익 x (1−공실일수/31) − 월 고정지출</span></span></p>
  <h2 class=scenario-title>순수익 시나리오</h2>
  <div class=scenario-chart id=o_schart></div>
  <div class=vac-scroll>
  <table><thead><tr><th>공실 일수</th><th>예약 일수</th><th>월 순익</th><th>연 순익</th></tr></thead>
  <tbody id=o_vac></tbody></table>
  </div>
</div>
  </div>
</div>
<div class="box market-card">
  <div class=market-head>
    <div><p class=vac-eyebrow>비슷한 매물과 비교하면</p><h3 class=market-title>나는 얼마나 벌고 있을까요?</h3></div>
    <div class=market-region-wrap>
      <button type=button class=market-region id=i_region_btn><span id=i_region_label>전국</span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg></button>
      <div class=market-region-menu id=i_region_menu></div>
    </div>
  </div>
  <div class=market-highlight>
    <div><p class=vac-eyebrow>내 예상 월 수익</p><p class=market-mine id=o_mymonth>—</p></div>
    <span id=o_mytop class=market-badge></span>
  </div>
  <div class=market-chart id=o_mchart></div>
  <div class=market-stats>
    <div><div class=vac-eyebrow>하위 25%</div><div class=market-stat-v id=o_mp25>-</div></div>
    <div><div class=vac-eyebrow>중앙값</div><div class=market-stat-v id=o_mmedian>-</div></div>
    <div><div class=vac-eyebrow>상위 25%</div><div class=market-stat-v id=o_mp75>-</div></div>
  </div>
  <p class=market-foot id=o_mfoot>-</p>
</div>
{% if not user %}
<div class=cta-row>
  <div class=cta-text>
    <p class=cta-h1>공실 걱정 없는 매물, 따로 있어요</p>
    <p class=cta-h2>수요는 많고 공급이 부족한 지역을 찾아 드릴게요.</p>
  </div>
  <a class=cta-btn href="/auth/signup">공급 부족 지역 보기<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg">
<path d="m9 18 6-6-6-6"/></svg></a>
</div>
{% endif %}
<script>
// design/culc_redesign/src/app/App.tsx의 useMemo 공식을 그대로 이식 (WPM=주/월 환산 상수)
var WPM=365/7/12
var SCENARIOS_DAYS=[3,6,10,15,20,25]
var REGIONS={{ market_regions|safe }}
var MARKET_ASOF={{ market_asof|tojson }}
var ICON_UP='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M16 7h6v6"/><path d="m22 7-8.5 8.5-5-5L2 17"/></svg>'
var ICON_DOWN='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M16 17h6v-6"/><path d="m22 17-8.5-8.5-5 5L2 7"/></svg>'
// 서버(portal.market_stats)가 net_profit 실집계를 넣어준다. 열린 구간은 JSON에 Infinity를
// 못 담아 null로 오므로 여기서 ±Infinity로 되살린다.
var MARKET_DATA={{ market_json|safe }}
Object.keys(MARKET_DATA).forEach(function(k){
  MARKET_DATA[k].bins.forEach(function(b){
    if(b.min===null) b.min=-Infinity
    if(b.max===null) b.max=Infinity
  })
})
var curRegion='전국'
function renderMarket(monthlyProfit){
  var activeTab=document.querySelector('.tab.active')
  var propLabel=activeTab?activeTab.dataset.type:'원룸'
  document.getElementById('i_region_label').textContent=curRegion+' '+propLabel
  var menu=document.getElementById('i_region_menu')
  menu.innerHTML=REGIONS.map(function(r){
    return '<button type=button class="market-region-opt'+(r===curRegion?' active':'')+
      '" data-region="'+r+'">'+r+' '+propLabel+'</button>'
  }).join('')
  menu.querySelectorAll('.market-region-opt').forEach(function(opt){
    opt.addEventListener('click',function(){
      curRegion=opt.dataset.region
      menu.classList.remove('show')
      document.getElementById('i_region_btn').classList.remove('open')
      calc()
    })
  })
  var region=curRegion
  var key=region+' · '+propLabel
  // 표본(30건) 미달 조합은 서버가 안 내려준다. 그때 전국으로 대신 보여주되, 그 사실을 아래에 밝힌다
  // (안 밝히면 '인천 원룸'이라 써놓고 전국 숫자를 보여주게 된다).
  var market=MARKET_DATA[key], fellBack=false
  if(!market){
    market=MARKET_DATA['전국 · '+propLabel]||MARKET_DATA['전국 · 원룸']
    fellBack=true
  }
  var userBinIdx=-1
  market.bins.forEach(function(b,i){if(monthlyProfit>=b.min&&monthlyProfit<b.max) userBinIdx=i})
  var aboveCount=0
  if(userBinIdx>=0) market.bins.slice(userBinIdx+1).forEach(function(b){aboveCount+=b.count})
  var topPct=userBinIdx<0?null:Math.round(aboveCount/market.total*100)

  document.getElementById('o_mymonth').textContent=monthlyProfit>0?(Math.round(monthlyProfit)+'만원'):'—'
  var badge=document.getElementById('o_mytop')
  if(topPct!==null&&monthlyProfit>0){badge.className='market-badge';badge.innerHTML=ICON_UP+'상위 '+topPct+'%';badge.style.display=''}
  else if(monthlyProfit<=0){badge.className='market-badge neg';badge.innerHTML=ICON_DOWN+'적자 구간';badge.style.display=''}
  else{badge.style.display='none'}

  var maxCount=Math.max.apply(null,market.bins.map(function(b){return b.count}))
  var chart=document.getElementById('o_mchart'),ch=''
  market.bins.forEach(function(b,i){
    var isUser=i===userBinIdx
    ch+='<div class=mbar-col><div class=mbar-track><div class="mbar'+(isUser?' user':'')+
        '" style="height:'+(b.count/maxCount*100)+'%"></div></div><span class=mbar-label>'+b.label+'</span></div>'
  })
  chart.innerHTML=ch

  document.getElementById('o_mp25').innerHTML=market.p25+'<span>만원</span>'
  document.getElementById('o_mmedian').innerHTML=market.median+'<span>만원</span>'
  document.getElementById('o_mp75').innerHTML=market.p75+'<span>만원</span>'
  document.getElementById('o_mfoot').innerHTML=
    (fellBack?'<b>'+region+' '+propLabel+'</b>은 표본이 적어 <b>전국</b> 기준으로 보여드려요<span class=sep>|</span>':'')+
    market.total.toLocaleString()+'건 기준(삼삼×네이버 매칭)<span class=sep>|</span>예약률 20% 이상 매물'+
    (MARKET_ASOF?'<span class=sep>|</span>'+MARKET_ASOF+' 크롤 기준':'')
}
function fmtWon(n,withSign){
  if(!isFinite(n)) return '∞'
  var neg=n<0, pfx=neg?'-':(withSign&&n>0?'+':''), abs=Math.abs(n)
  if(abs>=10000) return pfx+(abs/10000).toFixed(2)+'억원'
  if(abs<0.05) return pfx+'0만원'
  return pfx+Math.round(abs).toLocaleString()+'만원'
}
function calc(){
  var v=function(id){return parseFloat(document.getElementById(id).value)||0}
  var monthlyRent=v('i_rent'), deposit=v('i_dep'), depositRate=v('i_deprate')
  var mainFee=v('i_mgmt'), telecom=v('i_net'), supplies=v('i_clean'), rentalGoods=v('i_supply')
  var weeklyRent=v('i_wrent'), weeklyClean=v('i_wmgmt')
  var commission=v('i_fee'), vat=v('i_vat')
  // 슬라이더는 "예약일수"를 직접 받는다(0=한달공실~31=풀예약) — 기존 계산식은
  // 전부 공실일수(vacancy) 기준이라 여기서 한 번만 뒤집어서 넘겨준다
  var bookedDays=v('i_vacancy'), vacancy=31-bookedDays

  var weeklyGross=weeklyRent+weeklyClean
  var weeklyDeduct=weeklyGross*(commission+vat)/100
  var weeklyNet=weeklyGross-weeklyDeduct

  var depositInterest=deposit*depositRate/100/12
  var supplyMo=(supplies+rentalGoods)/10000*WPM
  var totalCost=monthlyRent+depositInterest+mainFee/10000+telecom/10000+supplyMo

  var monthlyNet100=weeklyNet*WPM   // 완전가동(공실 0일) 가정 월 순수익(비용 반영 전)
  var beVacancyPct=monthlyNet100>0?Math.min(99,Math.max(0,(1-totalCost/monthlyNet100)*100)):0
  var beVacancyDays=Math.floor(beVacancyPct/100*31)
  var beOccupiedRaw=totalCost/(monthlyNet100/31)
  var beOccupiedDays=isFinite(beOccupiedRaw)?Math.ceil(beOccupiedRaw):null   // 0/0 → NaN 가드

  var effRevenue=monthlyNet100*(1-vacancy/31)
  var monthlyProfit=effRevenue-totalCost
  var annualProfit=monthlyProfit*12
  var opMargin=effRevenue>0?(monthlyProfit/effRevenue)*100:(monthlyProfit>0?100:-100)

  var pos=monthlyProfit>0
  var safeVacancy=vacancy<=beVacancyDays

  var set=function(id,txt,cls){var e=document.getElementById(id);if(!e)return
    if(e.dataset.base===undefined) e.dataset.base=e.className
    e.textContent=txt; e.className=(e.dataset.base+' '+(cls||'')).trim()}

  set('o_fixedsum',fmtWon(totalCost))
  set('o_wnetbadge',fmtWon(weeklyNet))
  set('o_wrev_m',fmtWon(weeklyGross))
  set('o_ded_m','-'+fmtWon(weeklyDeduct))
  set('o_wnet_m',fmtWon(weeklyNet))

  document.getElementById('o_vacpct').textContent=bookedDays+'일 예약'
  document.getElementById('o_vacsub').textContent=vacancy+'일 공실'
  var heroEl=document.getElementById('o_heromonth')
  heroEl.textContent=fmtWon(monthlyProfit,true)
  heroEl.className='hero-num '+(pos?'pos-brand':'neg')
  document.getElementById('o_heroyear').textContent='연간 '+fmtWon(annualProfit,true)

  var insight=document.getElementById('o_vinsight')
  insight.className='vac-insight '+(safeVacancy?'safe':'unsafe')
  insight.textContent='월 '+bookedDays+'일 예약이면 '+(safeVacancy?
    '흑자예요. 아래에서 순수익을 확인해보세요.':
    ('적자예요. 손익분기는 월 '+(beOccupiedDays===null?'-':beOccupiedDays)+'일이에요.'))

  set('o_bevalue','월 '+(beOccupiedDays===null?'-':beOccupiedDays)+'일 예약')
  document.getElementById('o_besub').textContent='최대 '+beVacancyDays+'일 공실 허용'
  document.getElementById('o_kpi_be').className='kpi3-cell '+(safeVacancy?'warn':'neg')
  set('o_margin',opMargin.toFixed(1)+'%',opMargin>20?'pos':opMargin>0?'warn':'neg')
  document.getElementById('o_marginsub').textContent='공실 '+vacancy+'일 기준'
  document.getElementById('o_kpi_margin').className='kpi3-cell '+(opMargin>20?'posbg':opMargin>0?'warn':'neg')

  // 손익분기 슬라이더 — 안전구간 fill(0~beVacancyDays)은 고정, 손잡이는 현재 vacancy 위치
  document.getElementById('o_gbeflag').textContent=monthlyNet100>0?beVacancyDays:'-'
  document.getElementById('o_gsafe').style.width=Math.min(100,beVacancyDays/31*100)+'%'
  var thumb=document.getElementById('o_gthumb')
  thumb.style.left='calc('+Math.min(97,vacancy/31*100)+'% - 8px)'
  thumb.className='gauge2-thumb'+(safeVacancy?'':' unsafe')

  // 공실률별 시나리오 — App.tsx SCENARIOS_DAYS 그대로, 막대/표 행 클릭 시 공실일수 슬라이더 이동
  var scenarios=SCENARIOS_DAYS.map(function(days){
    return {days:days,profit:monthlyNet100*(1-days/31)-totalCost}
  })
  var maxAbs=Math.max.apply(null,scenarios.map(function(s){return Math.abs(s.profit)}))||1
  var chart=document.getElementById('o_schart'),ch=''
  scenarios.forEach(function(s){
    var active=s.days===vacancy
    var h=Math.max(4,Math.abs(s.profit)/maxAbs*100)
    var color=s.profit>=0?'var(--profit)':'var(--loss)'
    ch+='<div class="sbar-col'+(active?' active':'')+'" data-days='+s.days+'>'+
        '<div class=sbar-track><div class=sbar style="height:'+h+'%;background:'+color+';opacity:'+(active?1:.35)+'"></div></div>'+
        '<span class=sbar-label>'+s.days+'일</span></div>'
  })
  chart.innerHTML=ch
  chart.querySelectorAll('.sbar-col').forEach(function(col){
    col.addEventListener('click',function(){setv('i_vacancy',31-col.dataset.days);calc()})
  })

  var tb=document.getElementById('o_vac'),h2=''
  scenarios.forEach(function(s){
    var active=s.days===vacancy, bookedDays=31-s.days, cls=s.profit>=0?'pos-brand':'neg'
    h2+='<tr class="'+(active?'active':'')+'" data-days='+s.days+'>'+
       '<td><b>'+s.days+'일</b></td><td>'+bookedDays+'일</td>'+
       '<td class='+cls+'><b>'+fmtWon(s.profit,true)+'</b></td>'+
       '<td class='+cls+'>'+fmtWon(s.profit*12,true)+'</td></tr>'
  })
  tb.innerHTML=h2
  tb.querySelectorAll('tr').forEach(function(row){
    row.addEventListener('click',function(){setv('i_vacancy',31-row.dataset.days);calc()})
  })

  renderMarket(monthlyProfit)
}
document.getElementById('i_region_btn').addEventListener('click',function(e){
  e.stopPropagation()
  this.classList.toggle('open')
  document.getElementById('i_region_menu').classList.toggle('show')
})
document.addEventListener('click',function(){
  document.getElementById('i_region_menu').classList.remove('show')
  document.getElementById('i_region_btn').classList.remove('open')
})
// 매물유형 프리셋 — design/culc_redesign/src/app/App.tsx의 PRESETS/selectPropType 그대로 이식
var PRESETS={
  '원룸':{rent:60,dep:500,mgmt:150000,wrent:30,wmgmt:6},
  '빌라·주택':{rent:70,dep:1000,mgmt:150000,wrent:38,wmgmt:7},
  '오피스텔':{rent:80,dep:3000,mgmt:200000,wrent:45,wmgmt:7}
}
function setv(id,val){document.getElementById(id).value=val}
function selectPropType(btn){
  var type=btn.dataset.type
  if(btn.classList.contains('active')){
    // 이미 선택된 탭 재클릭 — 값은 그대로 두고 선택 표시만 해제
    btn.classList.remove('active')
    return
  }
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')})
  btn.classList.add('active')
  var p=PRESETS[type]
  setv('i_rent',p.rent); setv('i_dep',p.dep); setv('i_mgmt',p.mgmt)
  setv('i_wrent',p.wrent); setv('i_wmgmt',p.wmgmt)
  // 유형과 무관하게 항상 이 고정값으로 리셋(App.tsx selectPropType과 동일)
  setv('i_deprate',3.5); setv('i_net',20000); setv('i_clean',15000); setv('i_supply',20000)
  setv('i_fee',3.3); setv('i_vat',0); setv('i_vacancy',28)
  calc()
}
function resetAll(){
  ['i_rent','i_dep','i_deprate','i_mgmt','i_net','i_clean','i_supply',
   'i_wrent','i_wmgmt','i_fee','i_vat','i_vacancy'].forEach(function(id){setv(id,0)})
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')})
  calc()
}
document.querySelectorAll('.tab').forEach(function(btn){btn.addEventListener('click',function(){selectPropType(btn)})})
document.getElementById('btn_reset').addEventListener('click',resetAll)
document.querySelectorAll('input').forEach(function(e){e.addEventListener('input',calc)})
document.querySelectorAll('.info-ic').forEach(function(el){el.addEventListener('click',function(e){
  e.stopPropagation();el.parentElement.classList.toggle('show')})})
document.addEventListener('click',function(){document.querySelectorAll('.info-wrap.show').forEach(function(w){w.classList.remove('show')})})
calc()
</script>
</div></body></html>"""


@portal.route("/calc")
def calc():
    # 무료 공개 — 로그인 불필요(수익 계산기는 진입장벽 낮춰 가입 유도).
    # 로그인 상태면 auth._inject_nav가 공통 네비바를 자동 주입하므로, 여기 헤더는 비로그인일 때만 렌더.
    from flask import request as _rq
    mk = market_stats()      # 시장 비교 카드 실데이터(10분 캐시)
    return render_template_string(CALC_PAGE, user=current_user(), dong=_rq.args.get("dong", ""),
                                  rent=_rq.args.get("rent", type=int),
                                  dep=_rq.args.get("dep", type=int),
                                  market_json=json.dumps(mk["data"], ensure_ascii=False),
                                  market_regions=json.dumps(mk["regions"], ensure_ascii=False),
                                  market_asof=mk["asof"])


@portal.route("/map")
def fullmap():
    """전용 풀스크린 지도(카카오맵) — 행정동 폴리곤·렌트·부동산·추천 스팟.
    비로그인은 강남권(강남·서초·송파) 미리보기(demo). 부동산·추천은 가입 유도."""
    from map_view import MAP_PAGE
    # 지도용 JS 키는 로그인 앱(KAKAO_CLIENT_ID)과 별개 앱 → KAKAO_MAP_CLIENT_ID로 분리(이름 충돌 방지).
    return render_template_string(MAP_PAGE, kakao_js_key=os.environ.get("KAKAO_MAP_CLIENT_ID", ""),
                                  demo=(current_user() is None))


@portal.route("/og.png")
def og_image():
    """카톡/SNS 링크 미리보기 이미지(비로그인 접근 — auth의 og_image 예외)."""
    from flask import send_file
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "og.png"),
                     mimetype="image/png", max_age=86400)


@portal.route("/dong_geo.json")
def dong_geo():
    """수도권 행정동 경계(GeoJSON, mapshaper 8% 단순화) — /map 폴리곤 레이어용 정적 자산."""
    from flask import send_file
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dong_geo.json"),
                     mimetype="application/json", max_age=86400 * 7)


@portal.route("/")

def home():
    u = current_user()
    if not u:
        # 미로그인: 로그인 창 대신 서비스 소개 랜딩(무슨 서비스인지 보이게 → 이탈 방지).
        return render_template_string(PUBLIC_LANDING)
    # 매물 변동·인사이트는 모든 로그인 사용자에게, 접속자수는 관리자에게만.
    online = online_count() if u["role"] == "admin" else None
    return render_template_string(LANDING, user=u, churn=latest_listing_churn(),
                                  online=online, ins=dashboard_insights())


# 각 뷰어 앱(import 시 init_auth 적용됨)을 경로별로 마운트
from gangnam_app import app as gangnam_app  # noqa: E402
from profit_app import app as profit_app  # noqa: E402
from samsam_app import app as samsam_app  # noqa: E402

application = ProxyFix(
    DispatcherMiddleware(portal, {
        "/profit": profit_app,
        "/samsam": samsam_app,
        "/gangnam": gangnam_app,
    }),
    x_proto=1, x_host=1,
)


if __name__ == "__main__":
    from werkzeug.serving import run_simple
    print("통합 포털: http://127.0.0.1:8000")
    run_simple("0.0.0.0", 8000, application, use_reloader=True)
