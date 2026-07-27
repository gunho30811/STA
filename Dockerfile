# rendit 웹 포털 — Flask + gunicorn. ARM64/x86 공용(python 공식 이미지가 멀티아키).
# 프론트(frontend/dist)는 레포에 커밋돼 있어 빌드 불필요.
FROM python:3.11-slim

WORKDIR /app

# 시스템 의존: psycopg2-binary·pg8000 은 순수/휠이라 컴파일러 불필요. tzdata 로 KST 로그.
ENV TZ=Asia/Seoul PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# 의존성 먼저(레이어 캐시). requirements.txt + 프로덕션 WSGI 서버 gunicorn.
COPY requirements.txt .
RUN pip install -r requirements.txt gunicorn
# 통합채팅 삼삼 로그인용 크로미움(Playwright) — 계정 연결/재로그인을 서버에서 직접 처리.
# (없으면 로그인이 큐잉만 되고 GH Actions 대기 → 서버 단독으로 완결되도록 포함. ~300MB)
RUN python -m playwright install --with-deps chromium

# 앱 소스. .dockerignore 로 불필요한 것(.git, node_modules, __pycache__) 제외.
COPY . .

EXPOSE 8000

# gunicorn: WSGI(application)는 web/portal.py 에 있음. sys.path 에 web/ 추가는 portal 이 자체 처리.
#  - workers 3: 동기 워커(Flask). CPU·동시성에 따라 조정(Oracle ARM 4코어면 5~9로 올려도 됨).
#  - timeout 120: 크롤이 아닌 웹 요청은 짧지만, 최초 캐시 미스 계산 여유.
#  - PYTHONPATH 로 web/ 를 얹어 'portal:application' 을 찾게 함.
ENV PYTHONPATH=/app:/app/web:/app/common
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", \
     "--access-logfile", "-", "portal:application"]
