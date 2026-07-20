@echo off
REM 삼삼(33m2) 일일 크롤 — 로컬 PC 전용.
REM 삼삼 스케줄(예약) API가 데이터센터 IP를 소프트차단(200+빈 데이터)하므로
REM 오라클 서버가 아니라 가정용 IP인 이 PC에서 돌려야 예약률이 수집된다.
REM 윈도우 작업 스케줄러(rendit-samsam-daily)가 매일 새벽 실행. PC가 켜져 있어야 함.
setlocal
set LOG=%USERPROFILE%\rendit_crawl_samsam.log
set PY=C:\Users\ggi\AppData\Local\Programs\Python\Python315\python.exe
cd /d C:\STA

echo === %date% %time% samsam local daily start >> "%LOG%"
"%PY%" pipeline\samsam\crawler.py >> "%LOG%" 2>&1
"%PY%" pipeline\samsam\snapshot.py >> "%LOG%" 2>&1
"%PY%" pipeline\integrate\build_integrated.py >> "%LOG%" 2>&1
"%PY%" pipeline\refresh_insights.py >> "%LOG%" 2>&1
"%PY%" deploy\trigger_server_sync.py >> "%LOG%" 2>&1
echo === %date% %time% samsam local daily done >> "%LOG%"
