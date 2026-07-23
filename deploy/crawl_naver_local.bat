@echo off
REM 네이버 부동산 일일 크롤 — 로컬 PC 전용.
REM 네이버가 데이터센터 IP를 차단하므로 가정용 IP인 이 PC에서 돌린다.
REM DB 쓰기는 crawl_naver_local.py 가 Supabase에 적재 후 서버 sync로 오라클 반영.
REM 윈도우 작업 스케줄러(rendit-naver-daily)가 매일 02:00 실행. PC가 켜져 있어야 함.
setlocal
set LOG=%USERPROFILE%\rendit_crawl_naver.log
set PY=C:\Users\ggi\AppData\Local\Programs\Python\Python315\python.exe
cd /d C:\STA

echo === %date% %time% naver local-^>supabase-^>oracle start >> "%LOG%"
"%PY%" deploy\crawl_naver_local.py >> "%LOG%" 2>&1
echo === %date% %time% naver local-^>supabase-^>oracle done (rc=%errorlevel%) >> "%LOG%"
