# DEVLOG

Claude가 세션마다 진행한 작업을 날짜별로 기록하는 개발 일지.

## 2026-06-20

- 퍼블릭 전환 전 보안 점검: `data/config.json`에 삼삼엠투 계정 비밀번호가 평문으로 들어있던 것 발견.
  `classify_btype.py`를 `SAMSAM_EMAIL`/`SAMSAM_PASSWORD` 환경변수(`.env`, 없으면 실행 시 직접 입력)로
  바꾸고, `config.example.json` → `.env.example`로 교체. `.gitignore`에 `.env`, `data/config.json`,
  `__pycache__/` 추가.
- 초기 커밋 후 GitHub 퍼블릭 레포(`gunho30811/STA`) 생성, `main` 브랜치로 push 완료
  (`data/` 내 DB·jsonl 원본도 포함해서 커밋하기로 결정).
- 폴더 구조 정리: `web/`(Flask 앱 `app.py`, `profit_app.py`), `pipeline/`(크롤·가공 스크립트 6개)로 분리,
  `db.py`는 양쪽이 공유하므로 루트에 유지. 이동에 따라 경로 계산(BASE/DATA)과 `import db`/`import crawler`
  경로를 모두 수정하고 두 Flask 앱 재기동해서 정상 동작 확인.
- 인프라 아키텍처 결정 (구현은 아직 안 함, 결정만):
  - **DB**: Supabase(Postgres) 채택. 처음엔 Turso(SQLite 호환, 코드 변경 거의 없음)를 검토했으나,
    추후 로그인/결제 기능까지 갈 계획이라 Supabase가 Auth+Postgres+Stripe 연동까지 한 플랫폼에서
    되므로 이후 재마이그레이션 비용을 피하기 위해 선택. `db.py`는 Postgres 문법으로 다시 써야 함.
  - **앱 호스팅**: Railway. GitHub 연결 시 push마다 자동 배포, 콜드스타트 슬립이 없어 결제 기능 들어간
    서비스에 적합 (Render 무료 티어는 15분 비활성 슬립이라 부적합 판단).
  - **주간 크롤 스케줄**: GitHub Actions 스케줄 워크플로(cron). 서버 없이 GitHub의 클라우드 러너가
    매주 `pipeline/` 스크립트를 실행하고 Supabase에 직접 write, 실행 후 러너는 사라짐. 무료.
  - **데이터 흐름**: GitHub Actions(주 1회, 일시적) → Supabase Postgres(상시, 진짜 저장소) →
    Railway의 Flask 앱이 Supabase를 읽어 서빙. 로컬 `data/*.db`·git 커밋은 테스트용으로만 남기고
    추후 git 추적에서 제거 예정.
