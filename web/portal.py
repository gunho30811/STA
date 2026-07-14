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
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # web/

from flask import Flask, render_template_string
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix

from auth import current_user, init_auth

portal = Flask(__name__)
init_auth(portal)

LANDING = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>rendit · 단기임대 분석</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHZpZXdCb3g9JzAgMCAxMDAgMTAwJz48cmVjdCB3aWR0aD0nMTAwJyBoZWlnaHQ9JzEwMCcgcng9JzMwJyBmaWxsPScjNDMyMUYzJy8+PGcgZmlsbD0nbm9uZScgc3Ryb2tlPScjZmZmJyBzdHJva2Utd2lkdGg9JzE4JyBzdHJva2UtbGluZWNhcD0ncm91bmQnIHN0cm9rZS1saW5lam9pbj0ncm91bmQnPjxwYXRoIGQ9J00zOCAzNVY3MScvPjxwYXRoIGQ9J00zOCA1MUM0MSA0MiA0OSA0MCA1NyA0NCcvPjwvZz48L3N2Zz4=">

<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard-dynamic-subset.css">
<style>
*{box-sizing:border-box}body{margin:0;font-family:"Pretendard","Malgun Gothic",sans-serif;
background:linear-gradient(140deg,#0f172a,#1e293b);min-height:100vh;color:#e2e8f0;padding:40px 20px}
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
@media(max-width:640px){body{padding:24px 14px}h1{font-size:21px}.card{padding:18px}}
</style></head><body><div class=wrap>
<div class=top>
  <h1>ren<b style="color:#8b7dff">dit</b> <span style="font-size:15px;font-weight:600;color:#94a3b8">단기임대 수익성 분석</span></h1>
  <div class=who>{{user.name or user.username or user.email}} 님
    <a href="/auth/logout">로그아웃</a></div>
</div>
<p class=sub>부동산을 단기임대로 돌리면 얼마 버는지 · 회원 전용</p>
<div class=grid>
  <a class=card href="/profit/"><div class=ic>{ICON_PROFIT}</div><h2>통합 수익성</h2>
    <p>렌트 단기임대 풀가동 시 부동산 월세 대비 최대수익·순수익, 동/역 순위</p></a>
  <a class=card href="/samsam/"><div class=ic>{ICON_RENT}</div><h2>렌트 분석</h2>
    <p>옵션별 예약률 영향, 건물 인기(월순수익), 지역 예약률 트렌드</p></a>
  <a class=card href="/gangnam/"><div class=ic>{ICON_ESTATE}</div><h2>부동산 매물</h2>
    <p>수도권(서울·경기·인천) 부동산 매물 카드/상세 탐색</p></a>
</div>
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
</script>
</body></html>"""


PUBLIC_LANDING = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>rendit · 단기임대 수익성 분석</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHZpZXdCb3g9JzAgMCAxMDAgMTAwJz48cmVjdCB3aWR0aD0nMTAwJyBoZWlnaHQ9JzEwMCcgcng9JzMwJyBmaWxsPScjNDMyMUYzJy8+PGcgZmlsbD0nbm9uZScgc3Ryb2tlPScjZmZmJyBzdHJva2Utd2lkdGg9JzE4JyBzdHJva2UtbGluZWNhcD0ncm91bmQnIHN0cm9rZS1saW5lam9pbj0ncm91bmQnPjxwYXRoIGQ9J00zOCAzNVY3MScvPjxwYXRoIGQ9J00zOCA1MUM0MSA0MiA0OSA0MCA1NyA0NCcvPjwvZz48L3N2Zz4=">
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


@portal.route("/")
def home():
    u = current_user()
    if not u:
        # 미로그인: 로그인 창 대신 서비스 소개 랜딩(무슨 서비스인지 보이게 → 이탈 방지).
        return render_template_string(PUBLIC_LANDING)
    return render_template_string(LANDING, user=u)


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
