# -*- coding: utf-8 -*-
"""
회원/로그인 + 뷰어 게이트. 각 뷰어 앱이 init_auth(app) 만 호출하면:
  - 로그인 안 하면 /auth/login 으로 리다이렉트(모든 페이지 보호)
  - /auth/signup 가입(이름·생년월일·이메일·비밀번호), 비밀번호는 특수문자 필수
  - 이메일 인증(현재 목 모드: 코드를 화면/서버로그에 표시, SMTP 키 넣으면 실제 발송)
  - 관리자(gunho)는 /auth/members 에서 회원 관리

localhost 쿠키는 포트를 구분하지 않으므로 같은 SECRET_KEY를 쓰면 5001~5003 로그인이 공유된다.
"""
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import (Blueprint, redirect, render_template_string, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db

SPECIAL = r"""!@#$%^&*()_+\-=\[\]{};':"\\|,.<>/?~`"""
PW_RE = re.compile(f"[{re.escape(SPECIAL)}]")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_TTL_MIN = 10

bp = Blueprint("auth", __name__, url_prefix="/auth")


def _now():
    return dt.datetime.now()


def _gen_code():
    # Math.random/Date 불가 환경 회피: os.urandom 기반 6자리
    return f"{int.from_bytes(os.urandom(3), 'big') % 1000000:06d}"


def send_verify_email(email, code):
    """이메일 인증 코드 발송. 현재는 목(mock): 서버 로그 출력 + DEV면 화면에 노출.
    실제 발송 전환: 환경변수 SMTP_HOST/SMTP_USER/SMTP_PASS 넣고 이 함수에 SMTP 구현 추가."""
    print(f"[auth][MOCK EMAIL] {email} 인증코드: {code}", flush=True)
    return code  # 목 모드에선 코드를 그대로 돌려줘 화면에 보여줌


def pw_ok(pw):
    if len(pw or "") < 8:
        return "비밀번호는 8자 이상이어야 합니다."
    if not PW_RE.search(pw):
        return "비밀번호에 특수문자(!@#$ 등)를 반드시 포함해야 합니다."
    return None


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    r = db.connect().execute(
        "SELECT id,username,email,name,role,email_verified FROM users WHERE id=%s", (uid,)
    ).fetchone()
    return dict(r) if r else None


# ── 공통 페이지 셸 ──────────────────────────────────────────────────────────────
PAGE = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{{title}}</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:"Pretendard","Malgun Gothic",sans-serif;background:#0f172a;color:#1f2937;
display:flex;min-height:100vh;align-items:center;justify-content:center;padding:20px}
.box{background:#fff;border-radius:16px;padding:32px;width:100%;max-width:400px;box-shadow:0 20px 60px rgba(0,0,0,.3)}
h1{font-size:20px;margin:0 0 4px;font-weight:800}.sub{font-size:12.5px;color:#94a3b8;margin:0 0 20px}
label{font-size:12px;font-weight:700;color:#4b5563;display:block;margin:12px 0 4px}
input{width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px}
.btn{width:100%;margin-top:18px;padding:11px;border:none;border-radius:8px;background:#2563eb;color:#fff;font-size:14px;font-weight:700;cursor:pointer}
.btn:hover{background:#1d4ed8}
.msg{margin:12px 0;padding:10px 12px;border-radius:8px;font-size:13px}
.err{background:#fef2f2;color:#b91c1c}.ok{background:#ecfdf5;color:#047857}.info{background:#eff6ff;color:#1e40af}
.lnk{text-align:center;margin-top:16px;font-size:13px}.lnk a{color:#2563eb;text-decoration:none;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px 10px;border-bottom:1px solid #eef0f2;text-align:left}
th{background:#f9fafb}.danger{color:#dc2626;cursor:pointer;border:none;background:none;font-weight:700}
.code{font-size:24px;font-weight:800;letter-spacing:4px;color:#2563eb;text-align:center;background:#eff6ff;padding:12px;border-radius:8px;margin:8px 0}
</style></head><body><div class="box" style="{{boxstyle|default('')}}">{{body|safe}}</div></body></html>"""


def _render(title, body, boxstyle=""):
    return render_template_string(PAGE, title=title, body=body, boxstyle=boxstyle)


@bp.route("/login", methods=["GET", "POST"])
def login():
    msg = ""
    if request.method == "POST":
        lid = (request.form.get("login_id") or "").strip()
        pw = request.form.get("password") or ""
        conn = db.connect()
        r = conn.execute(
            "SELECT id,password_hash,role,email_verified FROM users WHERE username=%s OR email=%s",
            (lid, lid)).fetchone()
        if not r or not check_password_hash(r["password_hash"], pw):
            msg = '<div class="msg err">아이디(이메일) 또는 비밀번호가 올바르지 않습니다.</div>'
        elif r["role"] != "admin" and not r["email_verified"]:
            msg = '<div class="msg err">이메일 인증이 완료되지 않았습니다.</div>'
        else:
            session["uid"] = r["id"]
            session.permanent = True
            return redirect(request.args.get("next") or "/")
    body = f"""<h1>🔐 로그인</h1><p class="sub">부동산 단기임대 분석 · 회원 전용</p>{msg}
    <form method=post>
      <label>아이디 또는 이메일</label><input name=login_id autofocus>
      <label>비밀번호</label><input name=password type=password>
      <button class=btn>로그인</button>
    </form>
    <div class=lnk>계정이 없으신가요? <a href="{url_for('auth.signup')}">회원가입</a></div>"""
    return _render("로그인", body)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    msg = ""
    f = request.form
    if request.method == "POST":
        name = (f.get("name") or "").strip()
        birth = (f.get("birthdate") or "").strip()
        email = (f.get("email") or "").strip().lower()
        pw = f.get("password") or ""
        pw2 = f.get("password2") or ""
        err = None
        if not name or not birth or not email:
            err = "이름·생년월일·이메일을 모두 입력하세요."
        elif not EMAIL_RE.match(email):
            err = "이메일 형식이 올바르지 않습니다."
        elif pw != pw2:
            err = "비밀번호가 일치하지 않습니다."
        else:
            err = pw_ok(pw)
        if not err:
            conn = db.connect()
            if conn.execute("SELECT id FROM users WHERE email=%s", (email,)).fetchone():
                err = "이미 가입된 이메일입니다."
        if err:
            msg = f'<div class="msg err">{err}</div>'
        else:
            code = _gen_code()
            exp = (_now() + dt.timedelta(minutes=CODE_TTL_MIN)).isoformat(timespec="seconds")
            conn.execute(
                "INSERT INTO users(email,password_hash,name,birthdate,role,email_verified,"
                "verify_code,verify_expires,created_at) "
                "VALUES(%s,%s,%s,%s,'member',FALSE,%s,%s,%s)",
                (email, generate_password_hash(pw), name, birth, code, exp,
                 _now().isoformat(timespec="seconds")))
            conn.commit()
            shown = send_verify_email(email, code)
            return redirect(url_for("auth.verify", email=email, dev=shown))
    body = f"""<h1>📝 회원가입</h1><p class="sub">이름·생년월일·이메일 · 비밀번호는 특수문자 필수</p>{msg}
    <form method=post>
      <label>이름</label><input name=name value="{f.get('name','')}">
      <label>생년월일</label><input name=birthdate type=date value="{f.get('birthdate','')}">
      <label>이메일</label><input name=email type=email value="{f.get('email','')}">
      <label>비밀번호 (8자+ 특수문자 필수)</label><input name=password type=password>
      <label>비밀번호 확인</label><input name=password2 type=password>
      <button class=btn>인증메일 받기</button>
    </form>
    <div class=lnk>이미 회원이신가요? <a href="{url_for('auth.login')}">로그인</a></div>"""
    return _render("회원가입", body)


@bp.route("/verify", methods=["GET", "POST"])
def verify():
    email = (request.values.get("email") or "").strip().lower()
    msg = ""
    dev = request.args.get("dev")
    if dev:
        msg = (f'<div class="msg info">개발 모드: 인증코드가 이메일 대신 여기에 표시됩니다.</div>'
               f'<div class="code">{dev}</div>')
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        conn = db.connect()
        r = conn.execute(
            "SELECT id,verify_code,verify_expires,email_verified FROM users WHERE email=%s",
            (email,)).fetchone()
        if not r:
            msg = '<div class="msg err">가입 정보를 찾을 수 없습니다.</div>'
        elif r["email_verified"]:
            return redirect(url_for("auth.login"))
        elif not r["verify_code"] or r["verify_code"] != code:
            msg = '<div class="msg err">인증코드가 올바르지 않습니다.</div>'
        elif r["verify_expires"] and r["verify_expires"] < _now().isoformat(timespec="seconds"):
            msg = '<div class="msg err">인증코드가 만료되었습니다. 다시 가입해 주세요.</div>'
        else:
            conn.execute("UPDATE users SET email_verified=TRUE, verify_code=NULL WHERE id=%s",
                         (r["id"],))
            conn.commit()
            body = (f'<h1>✅ 인증 완료</h1><p class="sub">{email}</p>'
                    f'<div class="msg ok">이메일 인증이 완료됐습니다. 로그인해 주세요.</div>'
                    f'<div class=lnk><a href="{url_for("auth.login")}">로그인하러 가기</a></div>')
            return _render("인증 완료", body)
    body = f"""<h1>📧 이메일 인증</h1><p class="sub">{email} 로 보낸 6자리 코드를 입력하세요</p>{msg}
    <form method=post><input type=hidden name=email value="{email}">
      <label>인증코드</label><input name=code inputmode=numeric autofocus>
      <button class=btn>인증하기</button>
    </form>"""
    return _render("이메일 인증", body)


@bp.route("/members")
def members():
    u = current_user()
    if not u or u["role"] != "admin":
        return redirect(url_for("auth.login", next=request.path))
    conn = db.connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT id,email,name,birthdate,role,email_verified,created_at "
        "FROM users ORDER BY created_at DESC NULLS LAST").fetchall()]
    trs = ""
    for r in rows:
        vr = "✅" if r["email_verified"] else "⛔"
        delbtn = (f'<form method=post action="{url_for("auth.member_delete")}" style="margin:0">'
                  f'<input type=hidden name=id value="{r["id"]}">'
                  f'<button class=danger onclick="return confirm(\'삭제?\')">삭제</button></form>'
                  ) if r["role"] != "admin" else ""
        trs += (f"<tr><td>{r['id']}</td><td>{r.get('email') or '-'}</td><td>{r.get('name') or ''}</td>"
                f"<td>{r.get('birthdate') or ''}</td><td>{r['role']}</td><td>{vr}</td>"
                f"<td>{(r.get('created_at') or '')[:10]}</td><td>{delbtn}</td></tr>")
    body = f"""<h1>👥 회원 관리</h1><p class="sub">관리자: {u['username'] or u['email']} ·
      <a href="/">뷰어로</a> · <a href="{url_for('auth.logout')}">로그아웃</a></p>
    <table><thead><tr><th>ID</th><th>이메일</th><th>이름</th><th>생년월일</th><th>권한</th>
      <th>인증</th><th>가입일</th><th></th></tr></thead><tbody>{trs}</tbody></table>"""
    return _render("회원 관리", body, boxstyle="max-width:760px")


@bp.route("/members/delete", methods=["POST"])
def member_delete():
    u = current_user()
    if not u or u["role"] != "admin":
        return redirect(url_for("auth.login"))
    conn = db.connect()
    conn.execute("DELETE FROM users WHERE id=%s AND role<>'admin'",
                 (request.form.get("id"),))
    conn.commit()
    return redirect(url_for("auth.members"))


def init_auth(app):
    """앱에 로그인 게이트 적용. 모든 라우트를 보호하고 /auth/* 와 정적파일만 허용."""
    app.secret_key = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
    app.permanent_session_lifetime = dt.timedelta(days=14)
    try:
        db.init_db()
    except Exception as e:
        print(f"[auth] init_db 경고: {repr(e)[:80]}", flush=True)
    app.register_blueprint(bp)

    @app.before_request
    def _guard():
        ep = request.endpoint or ""
        if ep.startswith("auth.") or ep == "static":
            return None
        if not session.get("uid"):
            return redirect(url_for("auth.login", next=request.path))
        return None

    return app
