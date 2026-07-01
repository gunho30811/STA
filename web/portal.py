# -*- coding: utf-8 -*-
"""
통합 포털: 로그인 게이트 + 랜딩 + 3개 뷰어를 한 주소에 마운트.

  /            랜딩(로그인 필요) — 각 뷰어 링크
  /profit/...  통합 수익성(profit_app)
  /samsam/...  삼삼 분석(samsam_app)
  /gangnam/... 네이버 강남 매물(gangnam_app)
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

from auth import current_user, init_auth

portal = Flask(__name__)
init_auth(portal)

LANDING = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>부동산 단기임대 분석</title>
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
  <h1>🏠 부동산 단기임대 분석</h1>
  <div class=who>{{user.name or user.username or user.email}} 님
    <a href="/auth/logout">로그아웃</a></div>
</div>
<p class=sub>삼삼엠투 단기임대 × 네이버 월세 매칭 · 회원 전용</p>
<div class=grid>
  <a class=card href="/profit/"><div class=ic>💰</div><h2>통합 수익성</h2>
    <p>삼삼 단기임대 풀가동 시 네이버 월세 대비 최대수익·순수익, 동/역 순위</p></a>
  <a class=card href="/samsam/"><div class=ic>🛋️</div><h2>삼삼 분석</h2>
    <p>옵션별 예약률 영향, 건물 인기(월순수익), 지역 예약률 트렌드</p></a>
  <a class=card href="/gangnam/"><div class=ic>🏙️</div><h2>네이버 강남 매물</h2>
    <p>강남구 네이버 매물 카드/상세 탐색</p></a>
</div>
{% if user.role == 'admin' %}<div class=admin><a href="/auth/crawl">📊 크롤링 현황</a>
  &nbsp;·&nbsp; <a href="/auth/members">👥 회원 관리 →</a></div>{% endif %}
</div></body></html>"""


@portal.route("/")
def home():
    u = current_user() or {}
    return render_template_string(LANDING, user=u)


# 각 뷰어 앱(import 시 init_auth 적용됨)을 경로별로 마운트
from gangnam_app import app as gangnam_app  # noqa: E402
from profit_app import app as profit_app  # noqa: E402
from samsam_app import app as samsam_app  # noqa: E402

application = DispatcherMiddleware(portal, {
    "/profit": profit_app,
    "/samsam": samsam_app,
    "/gangnam": gangnam_app,
})


if __name__ == "__main__":
    from werkzeug.serving import run_simple
    print("통합 포털: http://127.0.0.1:8000")
    run_simple("0.0.0.0", 8000, application, use_reloader=False)
