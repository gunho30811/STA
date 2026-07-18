# rendit 배포 가이드 (Docker)

Flask 포털을 어느 서버에서든 `docker compose up` 한 방으로 띄운다. DB는 Supabase(관리형)를
그대로 쓰므로 컨테이너화하지 않고 `.env` 의 `DATABASE_URL` 로 연결한다.

**왜 Docker/자체서버:** Vercel(미국 리전)↔Supabase(서울)의 태평양 왕복이 페이지당 ~1초 +
서버리스 콜드스타트. 한국 서버(예: Oracle Cloud 춘천, 상시 VM)에 올리면 DB 왕복 10ms·콜드스타트 0.

## 준비물

- Docker + Docker Compose 가 깔린 리눅스 서버 (Oracle Cloud Free 등)
- `.env` 파일 (레포에 없음 — 시크릿). 최소: `DATABASE_URL`, `SECRET_KEY`, `ADMIN_USERNAME/PASSWORD`.
  선택: `DATA_GO_KR_KEY`, `DATA_SGIS_KR_ID/KEY`, `SAMSAM_EMAIL/PASSWORD`, `CF_TUNNEL_TOKEN`.

## 배포

```bash
git clone https://github.com/gunho30811/STA.git
cd STA
# .env 를 이 폴더에 복사(scp 등)

docker compose up -d --build      # web 상시 가동 (:8000)
docker compose logs -f web        # 로그 확인
```

`http://<서버IP>:8000` 접속. Oracle 은 **보안목록(Ingress)에서 8000(또는 80/443) 포트 개방** 필요.

## 외부 노출 / HTTPS

**옵션 A — Cloudflare Tunnel(추천, 무료·공유기설정 불필요):**
1. Cloudflare 대시보드 → Zero Trust → Tunnels → 터널 생성 → 토큰 복사
2. `.env` 에 `CF_TUNNEL_TOKEN=<토큰>` 추가
3. `docker compose --profile tunnel up -d`  → 지정 도메인으로 HTTPS 자동

**옵션 B — Caddy 리버스프록시(도메인 있으면):** 별도 Caddy 컨테이너로 `:443 → web:8000`,
Let's Encrypt 자동. (compose 에 caddy 서비스 추가하면 됨.)

## 운영

```bash
docker compose pull && docker compose up -d --build   # 코드 갱신 후 재배포
docker compose --profile crawl run --rm insights      # 대시보드·추천 캐시 수동 갱신
docker compose restart web                            # 재시작
```

- `restart: unless-stopped` 라 VM 재부팅·크래시 시 자동 복구.
- 크롤(삼삼/네이버)은 기존대로 GitHub Actions(한국 IP 필요분은 로컬 GUI)에서. 이 컨테이너는 웹만.
- 대시보드·추천 무거운 계산은 `kv_cache` 테이블에 미리 저장돼 즉시 응답(20초→0.2초).
  크롤 워크플로가 `refresh_insights.py` 로 갱신.

## 아키텍처 참고

- `python:3.11-slim` 공식 이미지라 **ARM64(Oracle Ampere A1)·x86 자동 대응**.
- gunicorn 워커 3개(기본). Ampere 4코어면 `Dockerfile` 의 `--workers` 를 5~9로 올려도 됨.
- 크롬/playwright 는 이미지에 없음(웹만). 크롤은 별도.
