@echo off
REM 삼삼(33m2) 일일 크롤 — 로컬 PC 전용, 오라클 서버 DB에 직접 적재.
REM 삼삼 스케줄(예약) API가 데이터센터 IP를 소프트차단하므로 가정용 IP인 이 PC에서 돌린다.
REM DB 쓰기는 crawl_samsam_local.py 가 SSH 터널로 오라클 pg에 직접 연결(Supabase 안 거침).
REM 윈도우 작업 스케줄러(rendit-samsam-daily)가 매일 05:00 실행. PC가 켜져 있어야 함.
setlocal
set LOG=%USERPROFILE%\rendit_crawl_samsam.log
set PY=C:\Users\ggi\AppData\Local\Programs\Python\Python315\python.exe
cd /d C:\STA

echo === %date% %time% samsam local-^>oracle start >> "%LOG%"
"%PY%" deploy\crawl_samsam_local.py >> "%LOG%" 2>&1
echo === %date% %time% samsam local-^>oracle done (rc=%errorlevel%) >> "%LOG%"
