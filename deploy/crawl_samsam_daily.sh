#!/bin/bash
# 삼삼(33m2) 일일 크롤 — 오라클 VM(한국 IP)에서 실행. GHA는 미국 IP 소프트차단으로 실패했음.
# 크롤러는 Supabase에 쓰고(크롤 계열의 단일 원본), 끝나면 로컬 pg로 즉시 동기화.
# ubuntu 유저 크론으로 실행(playwright 브라우저가 ~ubuntu/.cache에 있음).
set -e
exec >> /home/ubuntu/crawl_samsam.log 2>&1
echo "=== $(date '+%F %T') samsam daily start"
cd /home/ubuntu/STA
SUPA=$(grep '^SUPABASE_DATABASE_URL=' .env | cut -d= -f2-)
export DATABASE_URL="$SUPA"
PY=/home/ubuntu/crawlenv/bin/python

$PY pipeline/samsam/crawler.py
$PY pipeline/samsam/snapshot.py
$PY pipeline/integrate/build_integrated.py
$PY pipeline/refresh_insights.py

/home/ubuntu/STA/deploy/sync_from_supabase.sh
echo "=== $(date '+%F %T') samsam daily done"
