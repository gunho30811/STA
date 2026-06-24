import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Vercel 프로젝트별로 APP_NAME 환경변수로 앱 선택
# - sta-naver 프로젝트: APP_NAME=naver  (또는 미설정)
# - sta-profit 프로젝트: APP_NAME=profit
if os.environ.get("APP_NAME") == "profit":
    from web.profit_app import app
else:
    from web.app import app
