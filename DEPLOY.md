# rendit 배포 가이드

**프로덕션: <https://rendits.duckdns.org>** — Oracle Cloud Free VM(춘천, ARM Ampere 4코어/24GB)
`~/STA` 에 clone + Docker Compose. Caddy가 80/443을 받아 web(:8000)으로 프록시하고
Let's Encrypt HTTPS 자동 갱신. DuckDNS 무료 도메인(IP 갱신은 DuckDNS API).

**왜 자체서버:** Vercel(미국 리전)↔DB의 태평양 왕복이 페이지당 ~1초 + 서버리스 콜드스타트.
한국 VM + **서버 내부 Postgres**로 페이지 35~55ms.

## 구성 요약 (2026-07 현재)

| 구성요소 | 내용 |
|---|---|
| web | `docker compose` 의 `web` 서비스(gunicorn, :8000 내부 전용) |
| caddy | 서버 로컬 `docker-compose.override.yml`(레포 미포함)로 80/443 담당 |
| DB(서빙) | 서버 내부 `pg` 컨테이너(Postgres 17, 포트 미노출) — 웹의 `DATABASE_URL` |
| DB(크롤 원본) | Supabase — 크롤러들이 쓰는 곳. `SUPABASE_DATABASE_URL` 로 보존 |
| 동기화 | `deploy/sync_from_supabase.sh` — 6시간마다 + 크롤 직후, 크롤 데이터 테이블만 로컬로 |
| 회원·채팅 | **로컬 pg가 원본**(동기화 불가침) — `deploy/backup_users.sh` 가 매일 백업 |
| 크론 | `/etc/cron.d/rendit-sync`, `/etc/cron.d/rendit-crawl` |

## 코드 배포 (main 머지 후)

```bash
ssh ubuntu@<서버IP>
cd ~/STA && git pull && sudo docker compose up -d --build web
```

## 크롤 일과

- **네이버 수도권 오피스텔(증분)**: 매일 01:00 KST, 서버 크론 `deploy/crawl_naver_opst_daily.sh`
- **삼삼 전체**: 매일 로컬 PC(윈도우 작업 스케줄러 `rendit-samsam-daily`) —
  삼삼 **스케줄 API가 데이터센터 IP를 소프트차단**(200+빈 데이터)이라 가정용 IP 필수.
  `deploy/crawl_samsam_local.bat` 실행 → Supabase 적재 → 서버 동기화 트리거.
- **네이버 7종 전체(풀 리프레시)**: 매주 월요일, GitHub Actions `crawl.yml`.
- GHA `crawl-samsam.yml` 은 수동 실행 전용(데이터센터 IP 차단으로 스케줄 중단).

## 신규 서버에 처음 깔 때

```bash
git clone https://github.com/gunho30811/STA.git ~/STA
cd ~/STA   # .env 를 이 폴더에 복사(scp 등) — DATABASE_URL, SECRET_KEY, KAKAO_* 등
docker compose up -d --build
# Caddy/override, pg 컨테이너, 크론은 diary/2026-07-19~20.md 세팅 기록 참조
```

- Oracle은 **OCI 콘솔 보안목록(Ingress)** 이 실질 방화벽 — 80/443만 개방(5432 절대 금지).
- `restart: unless-stopped` 라 재부팅·크래시 자동 복구.
- 대시보드·추천 무거운 계산은 `kv_cache` 에 미리 저장(20초→0.2초), 크롤 후처리
  `pipeline/refresh_insights.py` 가 갱신.

## 아키텍처 참고

- `python:3.11-slim` 공식 이미지 — ARM64(Ampere)·x86 자동 대응.
- gunicorn 워커 3개(기본). 필요 시 `Dockerfile` 의 `--workers` 조정.
- 서버 크롤용 playwright는 컨테이너가 아니라 호스트 `~/crawlenv` venv에 설치(ubuntu 유저).
