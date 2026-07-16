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
import subway
from auth import current_user, init_auth, latest_listing_churn, online_count

portal = Flask(__name__)
init_auth(portal)

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
    dongs = conn.execute(
        "SELECT sigungu, dong, COUNT(*) n,"
        " AVG(LEAST(1.0, COALESCE(booked_days_1m,0)::float"
        "     / GREATEST(31-COALESCE(blocked_days_1m,0),1)))*100 occ"
        " FROM samsam_listings WHERE sido IN ('서울특별시','경기도','인천광역시')"
        " GROUP BY sigungu, dong HAVING COUNT(*) >= %s", (SPOT_MIN_RENT,)).fetchall()
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

    for c in cands:
        key = (c["sigungu"], c["dong"])
        g = prof.get(key)
        c["net"] = round(g[1] / g[2], 1) if g and g[2] else None          # 평균 월순수익(만원)
        c["margin"] = round(g[1] / g[0] * 100, 1) if g and g[0] > 0 else None  # 영업이익률(%)
        c["n_naver"] = nav.get(key, 0)
    return cands


def dashboard_insights():
    """공급부족 스팟 + 공장·신도시 예약률(10분 캐시). 실패 시 None(섹션 생략)."""
    now = time.time()
    if _INS_CACHE["data"] is not None and now - _INS_CACHE["t"] < 600:
        return _INS_CACHE["data"]
    try:
        conn = db.connect()
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
        conn.close()
        data = {"factories": factories, "newtowns": newtowns, "spots": spots}
        _INS_CACHE.update(t=now, data=data)
        return data
    except Exception:
        return None

LANDING = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>rendit · 단기임대 분석</title>
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
    <p>수도권(서울·경기·인천) 부동산 매물 카드/상세 탐색</p></a>
</div>
{% if churn %}
<div class=churn>
  <div class=churn-h>🔄 렌트 매물 변동 <span class=churn-d>최근 크롤 {{churn.date}}</span></div>
  <div class=churn-grid>
    {% for full, short in [('서울특별시','서울'),('경기도','경기'),('인천광역시','인천')] %}
    {% set c = churn.rows.get(full, {'added':0,'removed':0,'total':0}) %}
    <div class=churn-cell>
      <div class=cc-region>{{short}}</div>
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
    <a class="ins-cell spot-cell" href="/calc?dong={{s.dong}}{% if s.net is not none %}&net={{s.net}}{% endif %}">
      <div class=ins-name>{{s.sigungu}} <b>{{s.dong}}</b></div>
      <div class="ins-occ {{occcls(s.occ)}}">{{s.occ}}%</div>
      <div class=ins-sub>렌트 {{s.n_rent}}개 vs 부동산 {{'{:,}'.format(s.n_naver)}}개
        {% if s.net is not none %}<br>월순수익 <b style="color:{{'#34d399' if s.net>=0 else '#f87171'}}">{{s.net}}만</b>{% if s.margin is not none %} · 이익률 {{s.margin}}%{% endif %}{% else %}<br><span style="color:#475569">수익성 매칭 없음</span>{% endif %}</div>
    </a>
    {% endfor %}
  </div>
  <div class=spot-note>예약률=수요 · 렌트 매물수=경쟁(적을수록 기회) · 부동산 매물수=진입 가능한 월세 물건 · 카드 클릭 → 수익 계산기</div>
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
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHZpZXdCb3g9JzAgMCAxMDAgMTAwJz48Y2lyY2xlIGN4PSc1MCcgY3k9JzUwJyByPSc1MCcgZmlsbD0nIzQzMjFGMycvPjxnIGZpbGw9J25vbmUnIHN0cm9rZT0nI2ZmZicgc3Ryb2tlLXdpZHRoPScxMycgc3Ryb2tlLWxpbmVjYXA9J3JvdW5kJyBzdHJva2UtbGluZWpvaW49J3JvdW5kJz48cGF0aCBkPSdNNDAgMzRWNjcnLz48cGF0aCBkPSdNNDAgNDdDNDMgMzkgNTIgMzYgNjEgNDAnLz48L2c+PC9zdmc+">
<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard-dynamic-subset.css">
<style>
*{box-sizing:border-box}body{margin:0;font-family:"Pretendard","Malgun Gothic",sans-serif;
background:radial-gradient(1200px 600px at 50% -10%,#1e293b,#0f172a);min-height:100vh;color:#e2e8f0}
.nav{display:flex;justify-content:space-between;align-items:center;padding:18px 28px;max-width:1080px;margin:0 auto}
.brand{font-size:20px;font-weight:900;color:#fff;letter-spacing:-.02em}.brand .dot{color:#8b7dff}
.nav .cta{display:flex;gap:8px}
.nav a{text-decoration:none;font-weight:700;font-size:14px;padding:9px 16px;border-radius:9px}
.nav .login{color:#cbd5e1}.nav .signup{background:#4321F3;color:#fff}
.hero{max-width:1080px;margin:0 auto;padding:60px 28px 40px;text-align:center}
.hero .tag{display:inline-block;font-size:13px;font-weight:700;color:#93c5fd;background:rgba(67,33,243,.15);
border:1px solid rgba(96,165,250,.3);padding:6px 14px;border-radius:999px;margin-bottom:24px}
.hero h1{font-size:44px;line-height:1.2;font-weight:900;margin:0 0 18px;color:#fff;letter-spacing:-.03em}
.hero h1 .hl{color:#8b7dff}
.hero p{font-size:17px;line-height:1.7;color:#94a3b8;margin:0 auto 32px;max-width:640px}
.hero .btns{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}
.btn{text-decoration:none;font-weight:800;font-size:15px;padding:14px 28px;border-radius:11px}
.btn-primary{background:#4321F3;color:#fff;box-shadow:0 8px 24px rgba(67,33,243,.4)}
.btn-ghost{background:rgba(255,255,255,.06);color:#e2e8f0;border:1px solid rgba(255,255,255,.14)}
.cards{max-width:1080px;margin:20px auto 0;padding:20px 28px 70px;display:grid;
grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}
.card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:26px;text-align:left}
.card .ic{font-size:32px}.card h3{font-size:18px;font-weight:800;color:#fff;margin:12px 0 8px}
.card p{font-size:13.5px;color:#94a3b8;line-height:1.65;margin:0}
.steps{max-width:1080px;margin:0 auto;padding:0 28px 40px;color:#94a3b8;font-size:14px;text-align:center}
.steps b{color:#cbd5e1}
.foot{text-align:center;color:#64748b;font-size:12.5px;padding:30px 20px 50px}
@media(max-width:640px){.hero h1{font-size:32px}.hero{padding:40px 20px 30px}.nav{padding:14px 18px}}
</style></head><body>
<div class=nav>
  <div class=brand>ren<span class=dot>dit</span></div>
  <div class=cta>
    <a class=login href="/auth/login">로그인</a>
    <a class=signup href="/auth/signup">회원가입</a>
  </div>
</div>
<div class=hero>
  <span class=tag>🏠 부동산 단기임대 수익 분석</span>
  <h1>부동산 월세 매물,<br><span class=hl>단기임대로 돌리면 얼마 벌까?</span></h1>
  <p>네이버부동산 매물을 렌트(단기임대) 데이터와 매칭해, <b>월세로 줄 때 대비 얼마나 더 버는지</b>
     예약률·순수익까지 한눈에. 임대인·투자자를 위한 수익성 분석 도구입니다.</p>
  <div class=btns>
    <a class="btn btn-primary" href="/auth/signup">무료로 시작하기 →</a>
    <a class="btn btn-ghost" href="/profit/">🔍 데모 둘러보기</a>
  </div>
</div>
<div class=cards>
  <div class=card><div class=ic>{ICON_PROFIT}</div><h3>수익성 분석</h3>
    <p>단기임대 풀가동 시 부동산 월세 대비 최대수익·기대 월순수익. 동·역별 순위로 어디가 잘 나가는지 바로.</p></div>
  <div class=card><div class=ic>{ICON_RENT}</div><h3>렌트 데이터</h3>
    <p>옵션별 예약률 영향, 건물 인기 랭킹, 지역 예약률 트렌드까지 — 실제 단기임대 수요를 데이터로.</p></div>
  <div class=card><div class=ic>{ICON_ESTATE}</div><h3>부동산 매물</h3>
    <p>수도권(서울·경기·인천) 월세 매물을 근처 단기임대 수요와 함께. 이 집으로 운영 시 예상 순수익까지.</p></div>
</div>
<div class=steps>
  처음이신가요? &nbsp;<b>회원가입</b> → <b>수익성 탭</b>에서 순수익 높은 순으로 보기 → 관심 지역을 <b>순위</b>에서 확인하면 됩니다.
</div>
<div class=foot>회원 전용 서비스 · 가입 후 관리자 승인 시 이용 가능</div>
</body></html>"""


# rendit 아이콘 타일(퍼플 라운드 사각 + 흰 글리프, 로고의 둥근 기하학 톤). 다크/라이트 배경 공통.
def _tile(glyph):
    return (
        '<svg width="46" height="46" viewBox="0 0 46 46" fill="none" '
        'style="display:block">'
        '<rect width="46" height="46" rx="13" fill="#4321F3"/>'
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

for _k, _v in {"ICON_PROFIT": ICON_PROFIT, "ICON_RENT": ICON_RENT,
               "ICON_ESTATE": ICON_ESTATE}.items():
    LANDING = LANDING.replace("{" + _k + "}", _v)
    PUBLIC_LANDING = PUBLIC_LANDING.replace("{" + _k + "}", _v)


# ── 수익 계산기(/calc) — 사용자의 엑셀('천사의 도시') 로직 이식 ──────────────────
# 연원가 주간화(÷52) + 소모품 → 주 임대원가, 주매출 대비 순익·손익주·영업이익률,
# 공실률 시나리오별 연/월 순수익. 전부 클라이언트 JS로 실시간 계산.
CALC_PAGE = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>rendit · 수익 계산기</title>
<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard-dynamic-subset.css">
<style>
*{box-sizing:border-box}body{margin:0;font-family:"Pretendard","Malgun Gothic",sans-serif;
background:linear-gradient(140deg,#0f172a,#1e293b);min-height:100vh;color:#e2e8f0;padding:20px 14px 60px}
.wrap{max-width:900px;margin:0 auto}
h1{font-size:20px;font-weight:800;margin:4px 0 2px}
.sub{color:#94a3b8;font-size:12.5px;margin:0 0 18px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:700px){.cols{grid-template-columns:1fr}}
.box{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.09);border-radius:14px;padding:16px 18px}
.box h2{font-size:13.5px;font-weight:800;margin:0 0 12px;color:#cbd5e1}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:7px 0}
.row label{font-size:12.5px;color:#94a3b8}
.row input{width:110px;padding:8px 10px;border:1px solid #334155;border-radius:8px;background:#0f172a;
color:#e2e8f0;font-size:14px;text-align:right;font-weight:700}
.row .unit{font-size:11px;color:#64748b;width:34px}
.out .row{border-bottom:1px solid rgba(255,255,255,.05);padding:7px 0;margin:0}
.out b{font-size:15px}
.pos{color:#34d399}.neg{color:#f87171}.big{font-size:18px!important}
.vac{margin-top:14px}
.vac table{width:100%;border-collapse:collapse;font-size:12.5px}
.vac th,.vac td{padding:7px 6px;text-align:right;border-bottom:1px solid rgba(255,255,255,.06)}
.vac th{color:#94a3b8;font-weight:700}.vac td:first-child,.vac th:first-child{text-align:left}
.back{display:inline-block;margin-bottom:14px;color:#93c5fd;text-decoration:none;font-size:13px;font-weight:700}
.kpi{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:12px}
.kpi>div{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.07);border-radius:10px;padding:10px;text-align:center}
.kpi .l{font-size:11px;color:#64748b}.kpi .v{font-size:17px;font-weight:800;margin-top:3px}
</style></head><body><div class=wrap>
<a class=back href="/">← 대시보드</a>
<h1>🧮 단기임대 수익 계산기{% if dong %} — {{dong}}{% endif %}</h1>
<p class=sub>임차(내가 내는 비용)와 임대(투숙객에게 받는 돈)를 넣으면 주간 순익·손익주·영업이익률·공실률별 시나리오를 계산합니다. 연↔주 환산은 ÷52.</p>
<div class=cols>
  <div class=box><h2>💸 임차 원가 (내가 내는 돈)</h2>
    <div class=row><label>월 임차료</label><div><input id=i_rent type=number value=85><span class=unit>만원</span></div></div>
    <div class=row><label>보증금</label><div><input id=i_dep type=number value=1000><span class=unit>만원</span></div></div>
    <div class=row><label>보증금 이자율(연)</label><div><input id=i_deprate type=number value=5 step=0.5><span class=unit>%</span></div></div>
    <div class=row><label>월 관리비</label><div><input id=i_mgmt type=number value=30><span class=unit>만원</span></div></div>
    <div class=row><label>월 통신비</label><div><input id=i_net type=number value=3.3 step=0.1><span class=unit>만원</span></div></div>
    <div class=row><label>주당 청소 소모품</label><div><input id=i_clean type=number value=1000 step=100><span class=unit>원</span></div></div>
    <div class=row><label>주당 임대 소모품</label><div><input id=i_supply type=number value=1000 step=100><span class=unit>원</span></div></div>
  </div>
  <div class=box><h2>💰 임대 매출 (투숙객에게 받는 돈)</h2>
    <div class=row><label>주 임대료</label><div><input id=i_wrent type=number value=31><span class=unit>만원</span></div></div>
    <div class=row><label>주 관리비(청소비 등)</label><div><input id=i_wmgmt type=number value=12><span class=unit>만원</span></div></div>
    <div class=kpi>
      <div><div class=l>주 매출</div><div class=v id=o_wrev>-</div></div>
      <div><div class=l>주 임대원가</div><div class=v id=o_wcost>-</div></div>
      <div><div class=l>주 순익</div><div class=v id=o_wnet>-</div></div>
    </div>
    <div class=kpi>
      <div><div class=l>손익주</div><div class=v id=o_be>-</div></div>
      <div><div class=l>영업이익률</div><div class=v id=o_margin>-</div></div>
      <div><div class=l>연 임차원가</div><div class=v id=o_ycost>-</div></div>
    </div>
  </div>
</div>
<div class="box vac"><h2>📉 공실률 시나리오 (연 매출 × (1−공실률) − 연 임차원가)</h2>
  <table><thead><tr><th>공실률</th><th>연 매출</th><th>연 순수익</th><th>월 순수익</th></tr></thead>
  <tbody id=o_vac></tbody></table>
</div>
<script>
function won(x){return '₩'+Math.round(x).toLocaleString()}
function man(x){return (Math.round(x*10)/10).toLocaleString()+'만'}
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
  var set=function(id,txt,cls){var e=document.getElementById(id);e.textContent=txt;
    e.className='v'+(cls?' '+cls:'')}
  set('o_wrev',won(revW)); set('o_wcost',won(costW))
  set('o_wnet',won(netW),netW>=0?'pos':'neg')
  set('o_be',be?be.toFixed(2)+'주':'-'); set('o_margin',margin.toFixed(1)+'%',margin>=0?'pos':'neg')
  set('o_ycost',won(costY))
  var tb=document.getElementById('o_vac'),h=''
  ;[0,10,15,20,30].forEach(function(p){
    var ry=revY*(1-p/100), ny=ry-leaseY, cls=ny>=0?'pos':'neg'
    h+='<tr><td>'+p+'%</td><td>'+won(ry)+'</td><td class='+cls+'><b>'+won(ny)+'</b></td>'+
       '<td class='+cls+'>'+won(ny/12)+'</td></tr>'
  })
  tb.innerHTML=h
}
document.querySelectorAll('input').forEach(function(e){e.addEventListener('input',calc)})
calc()
</script>
</div></body></html>"""


@portal.route("/calc")
def calc():
    u = current_user()
    if not u:
        from flask import redirect as _rd
        return _rd("/auth/login?next=/calc")
    from flask import request as _rq
    return render_template_string(CALC_PAGE, dong=_rq.args.get("dong", ""))


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
