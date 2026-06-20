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
