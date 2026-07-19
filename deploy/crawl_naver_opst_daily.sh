#!/bin/bash
# 네이버 수도권 오피스텔 일일 크롤 — 오라클 VM에서 실행(주간 전체 크롤은 GHA crawl.yml 유지).
# crawl_state를 리셋하지 않는 증분 크롤: 신규/변경 위주로 매일 갱신.
# Supabase에 쓰고 끝나면 로컬 pg로 즉시 동기화.
set -e
exec >> /home/ubuntu/crawl_naver.log 2>&1
echo "=== $(date '+%F %T') naver OPST daily start"
cd /home/ubuntu/STA
SUPA=$(grep '^SUPABASE_DATABASE_URL=' .env | cut -d= -f2-)
export DATABASE_URL="$SUPA"
PY=/home/ubuntu/crawlenv/bin/python

$PY pipeline/naver/crawler.py --types OPST --sidos 서울시,경기도,인천시
$PY pipeline/naver/create_live_view.py
$PY pipeline/naver/crawl_detail.py --sidos 서울시,경기도,인천시 --exclude-types 상가
$PY pipeline/integrate/build_integrated.py
$PY pipeline/refresh_insights.py

/home/ubuntu/STA/deploy/sync_from_supabase.sh
echo "=== $(date '+%F %T') naver OPST daily done"
