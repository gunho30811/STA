# Vercel 진입점: 통합 포털 WSGI(application)를 그대로 노출
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "web"))

from web.portal import application as app  # noqa: E402,F401
