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
:root{--brand:#4D2EE9;--brand-hover:#3A1FC9;--brand-tint:#ECEAF8;--profit:#148A5E;--loss:#D24545;
--bg:#F4F6FB;--text:#1C1830;--text-sub:#6E6D68;--line:#E7E5DE;--gold:#D89700}
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
.wrap{max-width:900px;margin:0 auto}
.back{display:inline-block;margin-bottom:14px;color:var(--text-sub);text-decoration:none;font-size:13px;font-weight:700}
.back:hover{color:var(--brand)}
.ic{display:inline-flex;vertical-align:-0.15em;margin-right:6px}
.ic svg{width:1em;height:1em;display:block}
.ic-brand{color:var(--brand)}
h1{font-size:22px;font-weight:900;margin:4px 0 2px;letter-spacing:-.01em}
.sub{color:var(--text-sub);font-size:13px;margin:0 0 28px;line-height:1.6}
.sec-label{font-size:17px;font-weight:700;color:var(--text-sub);margin:0 0 9px}
/* align-items:start — 카드가 서로 키를 맞추느라 오른쪽에 빈 공간이 생기던 문제 */
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:32px;align-items:start}
@media(max-width:700px){.cols{grid-template-columns:1fr}}
.box{background:#fff;border:1px solid var(--line);border-radius:16px;padding:20px}
.card-head{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:4px}
.card-head h2{font-size:15px;font-weight:800;margin:0;color:var(--text)}
.card-head .unit-badge{font-size:11px;font-weight:700;color:var(--text-sub)}
.hint{font-size:12px;color:var(--text-sub);margin:0 0 16px}
.field-primary{margin:14px 0}
.field-primary label{display:block;font-size:13px;color:var(--text-sub);margin-bottom:6px}
.field-box{display:flex;align-items:center;justify-content:flex-end;gap:6px;border:1.5px solid var(--line);
border-radius:10px;padding:12px 14px;background:#fff}
.field-box:focus-within{border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-tint)}
.field-box input{border:none;outline:none;background:transparent;font-size:22px;font-weight:800;
color:var(--text);text-align:right;width:100%;font-family:inherit}
.field-box .unit{font-size:13px;color:var(--text-sub);font-weight:600;white-space:nowrap}
/* 접기(details) 없이 항상 펼친 상태 — 클릭해야 보이는 게 불친절하다는 피드백(장효령, 08-06) */
.more{margin-top:16px;border-top:1px solid var(--line);padding-top:14px}
.more-title{font-size:14px;font-weight:800;color:var(--text);line-height:1;margin:0 0 2px}
.field-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}
@media(max-width:480px){.field-grid{grid-template-columns:1fr}}
.field-sm label{display:block;font-size:12px;color:var(--text-sub);margin-bottom:5px}
.field-sm .field-box{padding:8px 10px}
.field-sm .field-box input{font-size:14px;font-weight:700}
.slider-row{margin-top:28px}
.slider-row .lbl{display:flex;justify-content:space-between;font-size:13px;color:var(--text-sub);margin-bottom:8px}
.slider-row .lbl b{color:var(--brand);font-size:14px}
.slider-row input[type=range]{width:100%;accent-color:var(--brand)}
.slider-row .scale{display:flex;justify-content:space-between;font-size:11px;color:var(--text-sub);margin-top:2px}
.pos{color:var(--profit)}.pos-brand{color:var(--brand)}.neg{color:var(--loss)}
.vac{margin-top:28px}
.vac h2{margin-top:0;display:flex;align-items:center;font-size:20px}
.vac h2 .ic{margin-right:7px}
.vac table{width:100%;border-collapse:collapse;font-size:12.5px}
.vac th,.vac td{padding:7px 6px;text-align:right;border-bottom:1px solid var(--line)}
.vac th{color:var(--text-sub);font-weight:800}.vac td:first-child,.vac th:first-child{text-align:left}
.hero-card{padding:24px 22px;border:none;box-shadow:0 2px 10px rgba(28,24,48,.06)}
.hero-sub{font-size:14px;color:var(--text-sub);margin:0 0 10px}
.hero-sub b{color:var(--text);font-weight:800}
/* 큰 숫자 아래에 '연 환산'을 배지로 — 한 줄에 붙여 두면 어디까지가 월/연인지 안 읽힌다 */
.hero-row{display:flex;flex-direction:column;align-items:flex-start;gap:12px}
.hero-num{font-size:34px;font-weight:700;letter-spacing:-.02em;white-space:nowrap}
.hero-annual{display:inline-block;background:#fff;border-radius:999px;padding:7px 16px;
font-size:14px;font-weight:800;color:var(--brand);white-space:nowrap;
box-shadow:0 1px 3px rgba(28,24,48,.08)}
@media(max-width:480px){.hero-num{font-size:27px}}
/* '받는 돈' 카드 안 주 단위 요약 — 입력하는 자리에서 바로 결과가 보이게(카드 여백도 채움) */
.week-mini{margin-top:20px;border-top:1px solid var(--line);padding-top:14px;display:grid;gap:9px}
.week-mini>div{display:flex;justify-content:space-between;align-items:baseline;font-size:13px}
.week-mini span{color:var(--text-sub)}
.week-mini b{font-weight:800;color:var(--text);font-variant-numeric:tabular-nums;white-space:nowrap}
.week-mini .hl{margin-top:3px;padding-top:11px;border-top:1px dashed var(--line)}
.week-mini .hl span{color:var(--text);font-weight:700}
.week-mini .hl b{font-size:17px}
/* 손익분기 게이지 — 한 달 30일 중 예약일(막대)과 손익분기일(눈금)을 겹쳐 여유를 한눈에 */
.gauge{margin-top:20px}
.gauge-track{position:relative;height:10px;border-radius:999px;background:#E9E6FB}
.gauge-fill{position:absolute;left:0;top:0;bottom:0;border-radius:999px;background:var(--brand);
transition:width .18s}
.gauge-mark{position:absolute;top:-5px;width:2.5px;height:20px;border-radius:2px;background:var(--gold);
transition:left .18s}
.gauge-cap{display:flex;justify-content:space-between;gap:10px;margin-top:9px;font-size:12.5px;
color:var(--text-sub);flex-wrap:wrap}
.gauge-cap b{color:var(--text);font-weight:800}
.gauge-cap .be b{color:var(--gold)}
.kpi3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:20px}
@media(max-width:700px){.kpi3{grid-template-columns:1fr}}
.kpi3-cell{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.kpi3-cell.warn{border-color:#F0DCA0;background:#FDF8ED}
.kpi3-cell .l{font-size:12.5px;color:var(--text-sub);margin-bottom:6px}
.kpi3-cell.warn .l{color:var(--gold);font-weight:700}
.kpi3-cell .v{font-size:20px;font-weight:800;white-space:nowrap}   /* '+145,385 / 원' 줄바꿈 방지 */
.kpi3-paren{font-size:13px;font-weight:600;color:var(--text-sub)}
.kpi3-sub{font-size:11.5px;color:var(--text-sub);margin-top:4px}
.calc-detail{margin-top:20px;background:#fff;border:1px solid var(--line);border-radius:16px;padding:20px}
.calc-detail .more-title{margin:0}
.more-hint{font-size:12px;color:var(--text-sub);margin:6px 0 0}
.calc-detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 24px;margin-top:14px}
@media(max-width:480px){.calc-detail-grid{grid-template-columns:1fr}}
.calc-detail-grid>div{display:flex;justify-content:space-between;font-size:13px}
.cd-l{color:var(--text-sub)}
.cd-v{font-weight:700;color:var(--text)}
.info-wrap{position:relative;display:inline-flex;align-items:center;margin-left:7px}
.info-ic{width:16px;height:16px;display:flex;align-items:center;justify-content:center;cursor:help}
.info-ic svg{width:16px;height:16px;display:block}
.info-tip{display:none;position:absolute;top:26px;left:-12px;background:#E4E4E7;color:var(--text);
font-size:13px;font-weight:600;padding:10px 14px;border-radius:12px;white-space:nowrap;z-index:5;
box-shadow:0 2px 8px rgba(0,0,0,.08)}
.info-tip::before{content:'';position:absolute;top:-5px;left:14px;width:11px;height:11px;background:#E4E4E7;
transform:rotate(45deg);border-radius:2px}
.info-wrap:hover .info-tip,.info-wrap.show .info-tip{display:block}
.be-row td{border-top:1px dashed var(--gold);border-bottom:none;color:var(--gold);font-size:11px;
font-weight:700;text-align:center;padding:6px 4px}
.cta-row{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;
margin-top:24px;padding:20px 22px;background:#fff;border:1px solid var(--line);border-radius:16px}
.cta-h1{font-size:14.5px;font-weight:800;color:var(--text);margin:0 0 4px}
.cta-h2{font-size:12.5px;color:var(--text-sub);margin:0}
.cta-btn{flex:0 0 auto;white-space:nowrap;background:var(--brand);color:#fff;font-weight:800;font-size:13.5px;
padding:12px 20px;border-radius:10px;text-decoration:none}
.cta-btn:hover{background:var(--brand-hover)}
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
<h1><span class="ic ic-brand"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M19 2H5C4.44772 2 4 2.44772 4 3V21C4 21.5523 4.44772 22 5 22H19C19.5523 22 20 21.5523 20 21V3C20 2.44772 19.5523 2 19 2Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M7 5.5H17V10H7V5.5Z" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M8.5 14C9.05228 14 9.5 13.5523 9.5 13C9.5 12.4477 9.05228 12 8.5 12C7.94772 12 7.5 12.4477 7.5 13C7.5 13.5523 7.94772 14 8.5 14Z" fill="currentColor"/>
<path d="M8.5 17C9.05228 17 9.5 16.5523 9.5 16C9.5 15.4477 9.05228 15 8.5 15C7.94772 15 7.5 15.4477 7.5 16C7.5 16.5523 7.94772 17 8.5 17Z" fill="currentColor"/>
<path d="M8.5 20C9.05228 20 9.5 19.5523 9.5 19C9.5 18.4477 9.05228 18 8.5 18C7.94772 18 7.5 18.4477 7.5 19C7.5 19.5523 7.94772 20 8.5 20Z" fill="currentColor"/>
<path d="M12 14C12.5523 14 13 13.5523 13 13C13 12.4477 12.5523 12 12 12C11.4477 12 11 12.4477 11 13C11 13.5523 11.4477 14 12 14Z" fill="currentColor"/>
<path d="M12 17C12.5523 17 13 16.5523 13 16C13 15.4477 12.5523 15 12 15C11.4477 15 11 15.4477 11 16C11 16.5523 11.4477 17 12 17Z" fill="currentColor"/>
<path d="M12 20C12.5523 20 13 19.5523 13 19C13 18.4477 12.5523 18 12 18C11.4477 18 11 18.4477 11 19C11 19.5523 11.4477 20 12 20Z" fill="currentColor"/>
<path d="M15.5 14C16.0523 14 16.5 13.5523 16.5 13C16.5 12.4477 16.0523 12 15.5 12C14.9477 12 14.5 12.4477 14.5 13C14.5 13.5523 14.9477 14 15.5 14Z" fill="currentColor"/>
<path d="M15.5 17C16.0523 17 16.5 16.5523 16.5 16C16.5 15.4477 16.0523 15 15.5 15C14.9477 15 14.5 15.4477 14.5 16C14.5 16.5523 14.9477 17 15.5 17Z" fill="currentColor"/>
<path d="M15.5 20C16.0523 20 16.5 19.5523 16.5 19C16.5 18.4477 16.0523 18 15.5 18C14.9477 18 14.5 18.4477 14.5 19C14.5 19.5523 14.9477 20 15.5 20Z" fill="currentColor"/>
</svg></span>단기임대 수익 계산기{% if dong %} — {{dong}}{% endif %}</h1>
<p class=sub>임차(내가 내는 비용)와 임대(투숙객에게 받는 돈)를 넣으면 월 순수익·손익분기·공실률별 시나리오를 계산합니다. (연↔주 환산은 ÷52로 계산합니다.){% if rent %} <b style="color:var(--brand)">{{dong}} 시세(월세 {{rent}}만·보증금 {{dep}}만)를 자동 입력했어요.</b>{% endif %}</p>
<div class=sec-label>입력</div>
<div class=cols>
  <div class=box>
    <div class=card-head><h2><span class="ic ic-brand"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M2 15L4.5 3H19.5L22 15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M2 15H7.455L8.3635 18H15.6365L16.5455 15H22V21.5H2V15Z" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
<path d="M15 10L12 7L9 10M12 7V13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg></span>내가 내는 돈</h2><span class=unit-badge>월 단위</span></div>
    <p class=hint>핵심 3가지만 넣으면 바로 계산돼요</p>
    <div class=field-primary><label>월 임차료</label>
      <div class=field-box><input id=i_rent type=number value={{rent or 85}}><span class=unit>만원</span></div></div>
    <div class=field-primary><label>보증금</label>
      <div class=field-box><input id=i_dep type=number value={{dep or 1000}}><span class=unit>만원</span></div></div>
    <div class=field-primary><label>월 관리비</label>
      <div class=field-box><input id=i_mgmt type=number value=30><span class=unit>만원</span></div></div>
    <div class=more>
      <p class=more-title>세부 비용 · 기본값 (바로 수정 가능)</p>
      <div class=field-grid>
        <div class=field-sm><label>보증금 이자율(연)</label>
          <div class=field-box><input id=i_deprate type=number value=5 step=0.5><span class=unit>%</span></div></div>
        <div class=field-sm><label>월 통신비</label>
          <div class=field-box><input id=i_net type=number value=3.3 step=0.1><span class=unit>만원</span></div></div>
        <div class=field-sm><label>주당 청소 소모품</label>
          <div class=field-box><input id=i_clean type=number value=1000 step=100><span class=unit>원</span></div></div>
        <div class=field-sm><label>주당 임대 소모품</label>
          <div class=field-box><input id=i_supply type=number value=1000 step=100><span class=unit>원</span></div></div>
      </div>
    </div>
  </div>
  <div class=box>
    <div class=card-head><h2><span class="ic ic-brand"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M2.5 15L5 3H19L21.5 15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M2.5 15H7.455L8.3635 18H15.6365L16.5455 15H21.5V21.5H2.5V15Z" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>
<path d="M15 10L12 13L9 10M12 13V7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg></span>받는 돈</h2><span class=unit-badge>주 단위</span></div>
    <p class=hint>단기임대는 주 단위로 받아요</p>
    <div class=field-primary><label>주 임대료</label>
      <div class=field-box><input id=i_wrent type=number value=31><span class=unit>만원</span></div></div>
    <div class=field-primary><label>주 관리비(청소비 등)</label>
      <div class=field-box><input id=i_wmgmt type=number value=12><span class=unit>만원</span></div></div>
    <div class=slider-row>
      <div class=lbl><span>기대 공실률</span><b id=o_vacpct>10%</b></div>
      <input type=range id=i_vacancy min=0 max=30 step=5 value=10>
      <div class=scale><span>0%</span><span>30%</span></div>
    </div>
    <div class=week-mini>
      <div><span>주 매출</span><b id=o_wrev_m>-</b></div>
      <div><span>주 임대원가</span><b id=o_wcost_m>-</b></div>
      <div class=hl><span>주 순익</span><b id=o_wnet_m>-</b></div>
    </div>
  </div>
</div>

<div class=sec-label>결과</div>
<div class="box hero-card">
  <p class=hero-sub>이 매물, 단기임대로 돌리면 <b id=o_herobadge>공실 10% 기준</b></p>
  <div class=hero-row>
    <span class=hero-num id=o_heromonth>월 순수익 -</span>
    <span class=hero-annual id=o_heroyear>연 환산 -</span>
  </div>
  <div class=gauge>
    <div class=gauge-track>
      <div class=gauge-fill id=o_gfill style="width:0%"></div>
      <i class=gauge-mark id=o_gmark style="left:0%"></i>
    </div>
    <div class=gauge-cap>
      <span>공실 <b id=o_gvac>-</b>% 기준 월 <b id=o_gdays>-</b>일 예약</span>
      <span class=be>손익분기 <b id=o_gbe>-</b>일 · 여유 <b id=o_gslack>-</b>일</span>
    </div>
  </div>
</div>
<div class=kpi3>
  <div class="kpi3-cell warn">
    <div class=l><span class=ic><svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path fill-rule="evenodd" clip-rule="evenodd" d="M1.25001 12C1.25001 6.063 6.06301 1.25 12 1.25C17.937 1.25 22.75 6.063 22.75 12C22.75 17.937 17.937 22.75 12 22.75C10.144 22.75 8.39501 22.279 6.87001 21.45L2.63701 22.237C2.51739 22.2591 2.39418 22.2519 2.278 22.2158C2.16183 22.1797 2.05617 22.1159 1.97015 22.0299C1.88413 21.9438 1.82032 21.8382 1.78424 21.722C1.74815 21.6058 1.74087 21.4826 1.76301 21.363L2.55101 17.13C1.69462 15.5559 1.24727 13.792 1.25001 12ZM12 7.25C12.1989 7.25 12.3897 7.32902 12.5303 7.46967C12.671 7.61032 12.75 7.80109 12.75 8V12C12.75 12.1989 12.671 12.3897 12.5303 12.5303C12.3897 12.671 12.1989 12.75 12 12.75C11.8011 12.75 11.6103 12.671 11.4697 12.5303C11.329 12.3897 11.25 12.1989 11.25 12V8C11.25 7.80109 11.329 7.61032 11.4697 7.46967C11.6103 7.32902 11.8011 7.25 12 7.25ZM12.567 16.501C12.6354 16.4283 12.6885 16.3426 12.7234 16.2491C12.7582 16.1556 12.774 16.056 12.7699 15.9563C12.7658 15.8565 12.7418 15.7586 12.6993 15.6683C12.6568 15.578 12.5968 15.497 12.5226 15.4302C12.4485 15.3634 12.3618 15.312 12.2675 15.2792C12.1733 15.2463 12.0734 15.2326 11.9738 15.2388C11.8742 15.245 11.7768 15.271 11.6874 15.3154C11.5979 15.3597 11.5183 15.4215 11.453 15.497L11.443 15.508C11.3746 15.5807 11.3215 15.6664 11.2867 15.7599C11.2518 15.8534 11.236 15.953 11.2401 16.0527C11.2443 16.1525 11.2683 16.2504 11.3107 16.3407C11.3532 16.431 11.4132 16.512 11.4874 16.5788C11.5615 16.6456 11.6483 16.697 11.7425 16.7298C11.8368 16.7627 11.9366 16.7764 12.0362 16.7702C12.1359 16.764 12.2332 16.738 12.3227 16.6936C12.4121 16.6493 12.4918 16.5875 12.557 16.512L12.567 16.501Z" fill="currentColor"/>
</svg></span>손익분기</div>
    <div class=v>월 약 <b id=o_bedays>-</b>일 <span class=kpi3-paren>(공실 <span id=o_bepct>-</span>%)</span></div>
    <div class=kpi3-sub>이 아래로 채우면 적자</div>
  </div>
  <div class=kpi3-cell>
    <div class=l>영업이익률</div>
    <div class=v id=o_margin>-</div>
  </div>
  <div class=kpi3-cell>
    <div class=l>주 순익</div>
    <div class=v id=o_wnet>-</div>
  </div>
</div>
<div class=calc-detail>
  <p class=more-title>계산 내역</p>
  <p class=more-hint>주 단위 값은 '받는 돈' 카드에 있어요 · 여기는 연 단위 기준</p>
  <div class=calc-detail-grid>
    <div><span class=cd-l>연 매출</span><span class=cd-v id=o_yrev>-</span></div>
    <div><span class=cd-l>연 임차원가</span><span class=cd-v id=o_ycost>-</span></div>
    <div><span class=cd-l>연 순수익 (공실 반영)</span><span class=cd-v id=o_ynet>-</span></div>
    <div><span class=cd-l>손익분기 예약 주수</span><span class=cd-v id=o_be>-</span></div>
  </div>
</div>

<div class="box vac"><h2><span class="ic ic-brand"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<g clip-path="url(#clip0_5_43)">
<path fill-rule="evenodd" clip-rule="evenodd" d="M17.188 1.07759C18.4702 0.819408 19.7785 0.713675 21.0855 0.762588C21.3874 0.77432 21.6738 0.89951 21.8874 1.11316C22.1011 1.3268 22.2263 1.61318 22.238 1.91509C22.268 2.62309 22.2715 4.11459 21.923 5.81259C21.718 6.81409 20.5135 7.08559 19.8495 6.42109L18.891 5.46309C18.5177 5.80077 18.1489 6.1433 17.7845 6.49059C16.839 7.39109 15.653 8.58159 14.6525 9.77009C13.8015 10.7811 12.169 10.7741 11.37 9.65509C10.8648 8.94564 10.3399 8.25037 9.796 7.57009C8.679 8.33459 6.36 10.0941 3.753 13.0751C3.591 13.2601 3.42833 13.4498 3.265 13.6441C3.09413 13.847 2.84967 13.9737 2.58538 13.9963C2.3211 14.019 2.05864 13.9357 1.85575 13.7648C1.65286 13.594 1.52615 13.3495 1.50351 13.0852C1.48086 12.8209 1.56413 12.5585 1.735 12.3556C1.907 12.1523 2.07767 11.9534 2.247 11.7591C5.042 8.56309 7.5405 6.68309 8.7435 5.86709C9.585 5.29659 10.689 5.49309 11.3065 6.25709C11.8914 6.98581 12.4552 7.73129 12.997 8.49259C13.0006 8.49715 13.005 8.50104 13.01 8.50409C13.0216 8.50902 13.0339 8.51174 13.0465 8.51209C13.0606 8.51306 13.0747 8.51087 13.0879 8.50568C13.101 8.50049 13.1129 8.49243 13.1225 8.48209C14.1905 7.21359 15.4355 5.96609 16.4045 5.04259C16.821 4.64609 17.1895 4.30609 17.475 4.04759L16.58 3.15059C15.9155 2.48659 16.187 1.28259 17.1885 1.07709M16.8825 21.3621C16.8165 20.4571 16.7505 18.8426 16.7505 15.9996C16.7505 13.1566 16.8165 11.5426 16.8825 10.6366C16.9555 9.63159 17.6895 8.84759 18.7335 8.78359C19.0655 8.76359 19.482 8.74959 20.0005 8.74959C20.519 8.74959 20.9355 8.76359 21.2675 8.78359C22.3115 8.84759 23.0455 9.63159 23.1185 10.6366C23.1845 11.5426 23.2505 13.1566 23.2505 15.9996C23.2505 18.8426 23.1845 20.4566 23.1185 21.3626C23.0455 22.3676 22.3115 23.1516 21.2675 23.2156C20.9355 23.2356 20.519 23.2496 20.0005 23.2496C19.482 23.2496 19.0655 23.2356 18.7335 23.2156C17.6895 23.1516 16.9555 22.3671 16.8825 21.3621ZM0.835 21.6351C0.7865 21.1631 0.75 20.4831 0.75 19.5001C0.75 18.5171 0.7865 17.8371 0.8345 17.3651C0.935 16.3701 1.7335 15.7996 2.608 15.7701C3.07186 15.7557 3.53592 15.7491 4 15.7501C4.5935 15.7501 5.0485 15.7586 5.392 15.7701C6.2665 15.7996 7.065 16.3701 7.1655 17.3651C7.2135 17.8371 7.25 18.5171 7.25 19.5001C7.25 20.4831 7.2135 21.1631 7.166 21.6351C7.065 22.6301 6.266 23.2006 5.392 23.2301C5.0485 23.2416 4.5935 23.2501 4 23.2501C3.4065 23.2501 2.9515 23.2416 2.608 23.2301C1.7335 23.2006 0.9355 22.6301 0.835 21.6351ZM8.75 18.0001C8.75 19.7056 8.7995 20.7821 8.8565 21.4586C8.9425 22.4826 9.7265 23.1821 10.7165 23.2256C11.0485 23.2401 11.4695 23.2501 12 23.2501C12.5305 23.2501 12.9515 23.2401 13.2835 23.2251C14.2735 23.1821 15.0575 22.4826 15.1435 21.4586C15.2005 20.7821 15.25 19.7056 15.25 18.0001C15.25 16.2946 15.2005 15.2181 15.144 14.5416C15.0575 13.5176 14.274 12.8181 13.2835 12.7746C12.9515 12.7601 12.5305 12.7501 12 12.7501C11.4695 12.7501 11.0485 12.7601 10.717 12.7751C9.7265 12.8181 8.942 13.5176 8.856 14.5416C8.8 15.2181 8.75 16.2946 8.75 18.0001Z" fill="currentColor"/>
</g>
<defs>
<clipPath id="clip0_5_43">
<rect width="24" height="24" fill="white"/>
</clipPath>
</defs>
</svg></span>공실률 시나리오<span class=info-wrap><span class=info-ic tabindex=0><svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="M11 9H13V7H11M12 20C7.59 20 4 16.41 4 12C4 7.59 7.59 4 12 4C16.41 4 20 7.59 20 12C20 16.41 16.41 20 12 20ZM12 2C10.6868 2 9.38642 2.25866 8.17317 2.7612C6.95991 3.26375 5.85752 4.00035 4.92893 4.92893C3.05357 6.8043 2 9.34784 2 12C2 14.6522 3.05357 17.1957 4.92893 19.0711C5.85752 19.9997 6.95991 20.7363 8.17317 21.2388C9.38642 21.7413 10.6868 22 12 22C14.6522 22 17.1957 20.9464 19.0711 19.0711C20.9464 17.1957 22 14.6522 22 12C22 10.6868 21.7413 9.38642 21.2388 8.17317C20.7363 6.95991 19.9997 5.85752 19.0711 4.92893C18.1425 4.00035 17.0401 3.26375 15.8268 2.7612C14.6136 2.25866 13.3132 2 12 2ZM11 17H13V11H11V17Z" fill="#4D2EE9"/>
</svg></span><span class=info-tip>연 매출 x (1-공실률) - 연 임차원가</span></span></h2>
  <table><thead><tr><th>공실률</th><th>월 예약(환산)</th><th>연 순수익</th><th>월 순수익</th></tr></thead>
  <tbody id=o_vac></tbody></table>
</div>
{% if not user %}
<div class=cta-row>
  <div class=cta-text>
    <p class=cta-h1 id=o_ctahead>이 매물은 한 달 약 -일까지 채우면 흑자예요.</p>
    <p class=cta-h2>그럼 애초에 공실 걱정 적은, 수요 높고 공급 부족한 자리는 어디일까요?</p>
  </div>
  <a class=cta-btn href="/auth/signup">★ 공급부족 스팟 보기</a>
</div>
{% endif %}
<script>
function won(x){return '₩'+Math.round(x).toLocaleString()}
function man(x){return (Math.round(x*10)/10).toLocaleString()+'만'}
function signed(x){return (x>=0?'+':'')+Math.round(x).toLocaleString()}
function calc(){
  var v=function(id){return parseFloat(document.getElementById(id).value)||0}
  var rentY=v('i_rent')*10000*12, depFee=v('i_dep')*10000*(v('i_deprate')/100),
      mgmtY=v('i_mgmt')*10000*12, netY=v('i_net')*10000*12
  var leaseY=rentY+depFee+mgmtY+netY            // 연 임차원가(소모품 제외)
  var leaseW=leaseY/52
  var supplyW=v('i_clean')+v('i_supply')
  var costW=leaseW+supplyW                       // 주 임대원가
  var costY=leaseY+supplyW*52
  var revW=(v('i_wrent')+v('i_wmgmt'))*10000, revY=revW*52
  var netW=revW-costW
  var be=revW>0?(costY/12)/revW:0                // 손익주=월원가/주매출
  var margin=revW>0?(revW-leaseW)/revW*100:0     // 영업이익률(소모품 제외 원가 기준)
  // 원래 클래스(.cd-v/.v 등)를 유지한 채 색상 클래스만 덧붙인다.
  // (예전엔 className을 'v'로 통째 덮어써서 계산 내역 값의 굵기가 사라졌다)
  var set=function(id,txt,cls){var e=document.getElementById(id);if(!e)return
    if(e.dataset.base===undefined) e.dataset.base=e.className
    e.textContent=txt; e.className=(e.dataset.base+' '+(cls||'')).trim()}
  set('o_wrev_m',won(revW)); set('o_wcost_m',won(costW))
  set('o_wnet_m',signed(netW)+'원',netW>=0?'pos':'neg')
  set('o_wnet',signed(netW)+'원',netW>=0?'pos':'neg')
  set('o_be',be?be.toFixed(2)+'주':'-'); set('o_margin',margin.toFixed(1)+'%')
  set('o_yrev',won(revY)); set('o_ycost',won(costY))
  var p=v('i_vacancy')
  document.getElementById('o_vacpct').textContent=p+'%'
  document.getElementById('o_herobadge').textContent='공실 '+p+'% 기준'
  var heroYear=revY*(1-p/100)-leaseY, heroMonth=heroYear/12
  var heroEl=document.getElementById('o_heromonth')
  heroEl.textContent='월 순수익 '+signed(heroMonth)+'원'
  heroEl.className='hero-num '+(heroMonth>=0?'pos-brand':'neg')
  document.getElementById('o_heroyear').textContent='연 환산 '+signed(heroYear)+'원'
  set('o_ynet',signed(heroYear)+'원',heroYear>=0?'pos':'neg')
  // 손익분기를 일 단위로 환산(1주=7일) — 30일 기준 월 예약일수·공실률로도 같이 표시(표시 전환일 뿐, be 값 자체는 그대로).
  var beDaysRaw=be*7
  var beDays=Math.max(0,Math.min(30,Math.round(beDaysRaw)))
  var bePct=Math.max(0,Math.min(100,Math.round(100*(1-beDaysRaw/30))))
  document.getElementById('o_bedays').textContent=revW>0?beDays:'-'
  document.getElementById('o_bepct').textContent=revW>0?bePct:'-'
  // 손익분기 게이지: 막대=이 공실률에서 채워지는 월 예약일, 눈금=흑자 전환에 필요한 일수
  var bookDays=Math.max(0,Math.min(30,Math.round(30*(1-p/100))))
  var bePos=Math.max(0,Math.min(30,beDaysRaw))
  document.getElementById('o_gfill').style.width=(bookDays/30*100)+'%'
  document.getElementById('o_gmark').style.left=(revW>0?bePos/30*100:0)+'%'
  document.getElementById('o_gvac').textContent=p
  document.getElementById('o_gdays').textContent=bookDays
  document.getElementById('o_gbe').textContent=revW>0?beDays:'-'
  document.getElementById('o_gslack').textContent=revW>0?Math.max(0,bookDays-beDays):'-'
  var ctaHead=document.getElementById('o_ctahead')
  if(ctaHead) ctaHead.innerHTML=revW>0?
    ('이 매물은 한 달 <span class=pos>약 '+beDays+'일</span>까지 채우면 흑자예요.'):'이 매물은 지금 조건으로는 흑자가 나지 않아요.'
  var tb=document.getElementById('o_vac'),h='', beShown=false
  ;[0,10,15,20,30].forEach(function(p){
    if(!beShown && revW>0 && p>bePct){
      h+='<tr class=be-row><td colspan=4>손익분기 (공실 '+bePct+'% · '+beDays+'일)</td></tr>'
      beShown=true
    }
    var days=Math.round(30*(1-p/100)), daysTxt=(p===0?'':'약 ')+days+'일'
    var ry=revY*(1-p/100), ny=ry-leaseY, cls=ny>=0?'pos-brand':'neg'
    h+='<tr><td>'+p+'%</td><td>'+daysTxt+'</td><td class='+cls+'><b>'+signed(ny)+'</b></td>'+
       '<td class='+cls+'>'+signed(ny/12)+'</td></tr>'
  })
  tb.innerHTML=h
}
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
    return render_template_string(CALC_PAGE, user=current_user(), dong=_rq.args.get("dong", ""),
                                  rent=_rq.args.get("rent", type=int),
                                  dep=_rq.args.get("dep", type=int))


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
