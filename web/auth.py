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
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests as _requests

from flask import (Blueprint, jsonify, redirect, render_template_string, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db

ONLINE_WINDOW_MIN = 5   # 이 시간 안에 핑이 온 세션만 "현재 접속중"으로 집계

SPECIAL = r"""!@#$%^&*()_+\-=\[\]{};':"\\|,.<>/?~`"""
PW_RE = re.compile(f"[{re.escape(SPECIAL)}]")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_TTL_MIN = 10
DAILY_SIGNUP_LIMIT = int(os.environ.get("DAILY_SIGNUP_LIMIT", 10))  # 하루 가입 한도(스팸 방지)

_KAKAO_CLIENT_ID = os.environ.get("KAKAO_CLIENT_ID", "")
_KAKAO_CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "")
_KAKAO_REDIRECT_URI = os.environ.get("KAKAO_REDIRECT_URI", "")

bp = Blueprint("auth", __name__, url_prefix="/auth")


def _now():
    return dt.datetime.now()


def _ping_visitor():
    """현재 접속자수 집계용 핑. 세션마다 최근 활동시각을 기록. 부가기능이라 실패해도 요청을 막지 않는다."""
    try:
        vid = session.get("vid")
        if not vid:
            vid = secrets.token_hex(8)
            session["vid"] = vid
        conn = db.connect()
        conn.execute(
            "INSERT INTO visitor_pings(session_id,last_seen) VALUES(%s,%s) "
            "ON CONFLICT (session_id) DO UPDATE SET last_seen=EXCLUDED.last_seen",
            (vid, _now().isoformat(timespec="seconds")))
        if secrets.randbelow(50) == 0:   # 가끔 오래된 핑 정리(별도 크론 없이 테이블 크기 관리)
            cutoff = (_now() - dt.timedelta(days=1)).isoformat(timespec="seconds")
            conn.execute("DELETE FROM visitor_pings WHERE last_seen < %s", (cutoff,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def online_count():
    """최근 ONLINE_WINDOW_MIN 분 안에 활동한 세션 수(현재 접속자). 실패 시 None."""
    try:
        conn = db.connect()
        cutoff = (_now() - dt.timedelta(minutes=ONLINE_WINDOW_MIN)).isoformat(timespec="seconds")
        n = conn.execute("SELECT COUNT(*) FROM visitor_pings WHERE last_seen >= %s",
                         (cutoff,)).fetchone()[0]
        conn.close()
        return n
    except Exception:
        return None


def latest_listing_churn():
    """가장 최근 크롤일의 수도권 시도별 매물 추가/삭제/총계.
    반환: {'date': 'YYYY-MM-DD', 'rows': {sido: {'added':a,'removed':r,'total':t}}} 또는 None."""
    try:
        conn = db.connect()
        d = conn.execute("SELECT MAX(crawl_date) FROM samsam_churn").fetchone()[0]
        if not d:
            conn.close()
            return None
        rows = conn.execute(
            "SELECT sido, added, removed, total FROM samsam_churn WHERE crawl_date=%s",
            (d,)).fetchall()
        conn.close()
        return {"date": d,
                "rows": {r[0]: {"added": r[1] or 0, "removed": r[2] or 0, "total": r[3] or 0}
                         for r in rows}}
    except Exception:
        return None


def _gen_code():
    # Math.random/Date 불가 환경 회피: os.urandom 기반 6자리
    return f"{int.from_bytes(os.urandom(3), 'big') % 1000000:06d}"


def _smtp_configured():
    return all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))


def send_verify_email(email, code):
    """이메일 인증 코드 발송.
    - SMTP_HOST/SMTP_USER/SMTP_PASS 가 설정돼 있으면 실제 발송 → None 반환(화면에 코드 미노출).
    - 미설정 또는 발송 실패 시 목(mock): 서버로그 출력 + 코드 반환(화면에 표시, 개발용).

    환경변수: SMTP_HOST, SMTP_PORT(기본 587), SMTP_USER, SMTP_PASS, SMTP_FROM(기본 SMTP_USER).
    Gmail 예: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_USER=계정, SMTP_PASS=앱비밀번호."""
    if not _smtp_configured():
        print(f"[auth][MOCK EMAIL] {email} 인증코드: {code}", flush=True)
        return code
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.utils import formataddr
        from email.header import Header

        host = os.environ["SMTP_HOST"]
        port = int(os.environ.get("SMTP_PORT", 587))
        user = os.environ["SMTP_USER"]
        pw = os.environ["SMTP_PASS"].replace(" ", "")  # 앱비번 공백 붙여넣기 허용
        sender = os.environ.get("SMTP_FROM", user)

        msg = MIMEText(
            f"안녕하세요.\n\n부동산 단기임대 분석 서비스 이메일 인증 코드입니다.\n\n"
            f"인증코드: {code}\n\n{CODE_TTL_MIN}분 내에 입력해 주세요.",
            "plain", "utf-8")
        msg["Subject"] = Header("[부동산분석] 이메일 인증 코드", "utf-8")
        msg["From"] = formataddr((str(Header("부동산 단기임대 분석", "utf-8")), sender))
        msg["To"] = email

        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as s:
                s.login(user, pw)
                s.sendmail(sender, [email], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                s.starttls()
                s.login(user, pw)
                s.sendmail(sender, [email], msg.as_string())
        print(f"[auth][EMAIL] {email} 인증코드 발송 완료", flush=True)
        return None   # 실제 발송됨 → 화면에 코드 노출 안 함
    except Exception as e:
        print(f"[auth][EMAIL][ERROR] {email} 발송 실패({repr(e)[:80]}) → 목 폴백", flush=True)
        return code


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
    conn = db.connect()
    try:
        r = conn.execute(
            "SELECT id,username,email,name,role,email_verified FROM members WHERE id=%s", (uid,)
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


# ── 공통 페이지 셸 ──────────────────────────────────────────────────────────────
PAGE = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{{title}}</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHZpZXdCb3g9JzAgMCAxMDAgMTAwJz48Y2lyY2xlIGN4PSc1MCcgY3k9JzUwJyByPSc1MCcgZmlsbD0nIzQzMjFGMycvPjxnIGZpbGw9J25vbmUnIHN0cm9rZT0nI2ZmZicgc3Ryb2tlLXdpZHRoPScxMycgc3Ryb2tlLWxpbmVjYXA9J3JvdW5kJyBzdHJva2UtbGluZWpvaW49J3JvdW5kJz48cGF0aCBkPSdNNDAgMzRWNjcnLz48cGF0aCBkPSdNNDAgNDdDNDMgMzkgNTIgMzYgNjEgNDAnLz48L2c+PC9zdmc+">
<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard-dynamic-subset.css">
<style>
*{box-sizing:border-box}body{margin:0;font-family:"Pretendard","Malgun Gothic",sans-serif;background:#0f172a;color:#1f2937;
display:flex;min-height:100vh;align-items:center;justify-content:center;padding:20px}
.box{background:#fff;border-radius:16px;padding:32px;width:100%;max-width:400px;box-shadow:0 20px 60px rgba(0,0,0,.3)}
h1{font-size:20px;margin:0 0 4px;font-weight:800}.sub{font-size:12.5px;color:#94a3b8;margin:0 0 20px}
label{font-size:12px;font-weight:700;color:#4b5563;display:block;margin:12px 0 4px}
input{width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;font-family:inherit}
input:focus{outline:none;border-color:#4321F3;box-shadow:0 0 0 3px rgba(67,33,243,.1)}
.btn{display:block;width:100%;margin-top:16px;padding:11px;border:none;border-radius:8px;
background:#4321F3;color:#fff;font-size:14px;font-weight:700;cursor:pointer;text-align:center;font-family:inherit}
.btn:hover{background:#3517c4}
.kakao-btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;margin-top:0;
padding:12px;border-radius:8px;background:#FEE500;color:#191919;font-size:14px;font-weight:700;
text-decoration:none;font-family:inherit}
.kakao-btn:hover{background:#f5dc00}
.kakao-btn svg{flex-shrink:0}
.or-sep{display:flex;align-items:center;gap:10px;margin:18px 0;color:#94a3b8;font-size:12px}
.or-sep hr{flex:1;border:none;border-top:1px solid #e5e7eb}
.msg{margin:12px 0;padding:10px 12px;border-radius:8px;font-size:13px}
.err{background:#fef2f2;color:#b91c1c}.ok{background:#ecfdf5;color:#047857}.info{background:#eff6ff;color:#1e40af}
.lnk{text-align:center;margin-top:20px;font-size:13px;color:#6b7280}.lnk a{color:#4321F3;text-decoration:none;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px 10px;border-bottom:1px solid #eef0f2;text-align:left}
th{background:#f9fafb;font-weight:700}.danger{color:#dc2626;cursor:pointer;border:none;background:none;font-weight:700}
.code{font-size:24px;font-weight:800;letter-spacing:4px;color:#4321F3;text-align:center;background:#eff6ff;padding:12px;border-radius:8px;margin:8px 0}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch}
@media(max-width:640px){body{padding:12px}.box{padding:22px 16px}
table{font-size:12px}th,td{padding:7px 8px}input{font-size:16px}}
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
            "SELECT id,password_hash,role,email_verified,approved FROM members WHERE username=%s OR email=%s",
            (lid, lid)).fetchone()
        if not r or not check_password_hash(r["password_hash"], pw):
            msg = '<div class="msg err">아이디(이메일) 또는 비밀번호가 올바르지 않습니다.</div>'
        elif r["role"] != "admin" and not r["email_verified"]:
            msg = '<div class="msg err">이메일 인증이 완료되지 않았습니다.</div>'
        elif r["role"] != "admin" and not r["approved"]:
            msg = '<div class="msg err">관리자 승인 대기 중입니다. 승인 후 로그인할 수 있습니다.</div>'
        else:
            session["uid"] = r["id"]
            session["role"] = r["role"]
            session.permanent = True
            return redirect(request.args.get("next") or "/")
    _ico = ('<svg width="18" height="18" viewBox="0 0 18 18"><path fill="#191919" d="M9 1.5C4.86'
            ' 1.5 1.5 4.19 1.5 7.5c0 2.1 1.27 3.94 3.19 5.06l-.81 3.01 3.48-2.29C7.72 13.42'
            ' 8.35 13.5 9 13.5c4.14 0 7.5-2.69 7.5-6S13.14 1.5 9 1.5z"/></svg>')
    body = (f'<h1 style="letter-spacing:-.02em;color:#171A23">ren<b style="color:#4321F3">dit</b></h1>'
            f'<p class="sub">부동산 단기임대 수익성 분석 · 회원 전용</p>{msg}'
            f'<a href="{url_for("auth.kakao_login")}" class="kakao-btn">{_ico}카카오로 시작하기</a>'
            f'<div class="or-sep"><hr><span>또는</span><hr></div>'
            f'<form method=post>'
            f'<label>아이디 또는 이메일</label><input name=login_id autofocus>'
            f'<label>비밀번호</label><input name=password type=password>'
            f'<button class=btn>이메일로 로그인</button>'
            f'</form>'
            f'<div class=lnk>처음이신가요?'
            f' <a href="{url_for("auth.signup")}">이메일로 가입</a>'
            f'&ensp;·&ensp;카카오 계정은 위 버튼으로 바로 가입됩니다</div>')
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
        existing = None
        if not err:
            conn = db.connect()
            existing = conn.execute(
                "SELECT id, email_verified FROM members WHERE email=%s", (email,)).fetchone()
            # 인증 완료된 계정만 '이미 가입'으로 막는다. 미인증 레코드(가입 도중 이탈·더블클릭·
            # 뒤로가기로 생김)는 재시도로 간주해 아래에서 갱신·재발송한다 —
            # "처음 가입하는데 이미 가입됐다며 막히고, 메일은 이미 옴" 버그의 원인이 이 미인증 잔재.
            if existing and existing["email_verified"]:
                err = "이미 가입된 이메일입니다. 로그인해 주세요."
        # 하루 가입 한도는 '신규 가입'에만 적용(미인증 재시도는 새 가입이 아니므로 제외).
        if not err and existing is None:
            today = _now().date().isoformat()
            cnt = conn.execute(
                "SELECT count(*) FROM members WHERE role='member' AND created_at >= %s",
                (today,)).fetchone()[0]
            if cnt >= DAILY_SIGNUP_LIMIT:
                err = f"오늘 가입 한도({DAILY_SIGNUP_LIMIT}명)에 도달했습니다. 내일 다시 시도해 주세요."
        if err:
            msg = f'<div class="msg err">{err}</div>'
        else:
            code = _gen_code()
            exp = (_now() + dt.timedelta(minutes=CODE_TTL_MIN)).isoformat(timespec="seconds")
            if existing:   # 미인증 레코드 재사용: 입력값·인증코드 갱신 후 재발송
                conn.execute(
                    "UPDATE members SET password_hash=%s,name=%s,birthdate=%s,"
                    "verify_code=%s,verify_expires=%s WHERE id=%s",
                    (generate_password_hash(pw), name, birth, code, exp, existing["id"]))
            else:
                conn.execute(
                    "INSERT INTO members(email,password_hash,name,birthdate,role,email_verified,"
                    "verify_code,verify_expires,created_at) "
                    "VALUES(%s,%s,%s,%s,'member',FALSE,%s,%s,%s)",
                    (email, generate_password_hash(pw), name, birth, code, exp,
                     _now().isoformat(timespec="seconds")))
            conn.commit()
            shown = send_verify_email(email, code)
            return redirect(url_for("auth.verify", email=email, dev=shown))
    body = (f'<h1>이메일로 가입</h1>'
            f'<p class="sub">이름·생년월일·이메일 · 비밀번호는 특수문자 필수</p>{msg}'
            f'<form method=post onsubmit="var b=this.querySelector(\'button\');'
            f'if(b)setTimeout(function(){{b.disabled=true;b.textContent=\'처리 중…\';}},0)">'
            f'<label>이름</label><input name=name value="{f.get("name","")}">'
            f'<label>생년월일</label><input name=birthdate type=date value="{f.get("birthdate","")}">'
            f'<label>이메일</label><input name=email type=email value="{f.get("email","")}">'
            f'<label>비밀번호 (8자+ 특수문자 필수)</label><input name=password type=password>'
            f'<label>비밀번호 확인</label><input name=password2 type=password>'
            f'<button class=btn>인증메일 받기</button>'
            f'</form>'
            f'<div class=lnk>'
            f'카카오 계정은 <a href="{url_for("auth.login")}">로그인 화면</a>에서 바로 가입 가능'
            f'&ensp;·&ensp;<a href="{url_for("auth.login")}">로그인</a></div>')
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
            "SELECT id,verify_code,verify_expires,email_verified FROM members WHERE email=%s",
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
            conn.execute("UPDATE members SET email_verified=TRUE, verify_code=NULL WHERE id=%s",
                         (r["id"],))
            conn.commit()
            body = (f'<h1>✅ 인증 완료</h1><p class="sub">{email}</p>'
                    f'<div class="msg ok">이메일 인증이 완료됐습니다.<br>'
                    f'<b>관리자 승인 후</b> 로그인할 수 있습니다. 승인되면 안내해 드립니다.</div>'
                    f'<div class=lnk><a href="{url_for("auth.login")}">로그인 화면으로</a></div>')
            return _render("인증 완료", body)
    body = f"""<h1>📧 이메일 인증</h1><p class="sub">{email} 로 보낸 6자리 코드를 입력하세요</p>{msg}
    <form method=post><input type=hidden name=email value="{email}">
      <label>인증코드</label><input name=code inputmode=numeric autofocus>
      <button class=btn>인증하기</button>
    </form>
    <form method=post action="{url_for('auth.resend')}" style="margin-top:10px">
      <input type=hidden name=email value="{email}">
      <button class=btn style="background:#64748b">인증번호 다시 보내기</button>
    </form>"""
    return _render("이메일 인증", body)


@bp.route("/resend", methods=["POST"])
def resend():
    email = (request.form.get("email") or "").strip().lower()
    conn = db.connect()
    r = conn.execute(
        "SELECT id,email_verified,verify_expires FROM members WHERE email=%s", (email,)
    ).fetchone()
    if not r:
        return redirect(url_for("auth.signup"))
    if r["email_verified"]:
        return redirect(url_for("auth.login"))
    # 쿨다운: 직전 발송 후 60초 이내면 재발송 막음(verify_expires = 발송시각 + CODE_TTL_MIN 으로 역산)
    if r["verify_expires"]:
        try:
            last_sent = dt.datetime.fromisoformat(r["verify_expires"]) - dt.timedelta(minutes=CODE_TTL_MIN)
            wait = 60 - (_now() - last_sent).total_seconds()
            if wait > 0:
                body = (f'<h1>📧 이메일 인증</h1><p class="sub">{email}</p>'
                        f'<div class="msg err">잠시 후 다시 시도해 주세요({int(wait)}초 뒤 재발송 가능).</div>'
                        f'<div class=lnk><a href="{url_for("auth.verify", email=email)}">인증 화면으로</a></div>')
                return _render("이메일 인증", body)
        except (ValueError, TypeError):
            pass
    code = _gen_code()
    exp = (_now() + dt.timedelta(minutes=CODE_TTL_MIN)).isoformat(timespec="seconds")
    conn.execute("UPDATE members SET verify_code=%s, verify_expires=%s WHERE id=%s",
                 (code, exp, r["id"]))
    conn.commit()
    shown = send_verify_email(email, code)
    return redirect(url_for("auth.verify", email=email, dev=shown))


@bp.route("/members")
def members():
    u = current_user()
    if not u or u["role"] != "admin":
        return redirect(url_for("auth.login", next=request.path))
    conn = db.connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT id,email,name,birthdate,role,email_verified,approved,created_at "
        "FROM members ORDER BY approved ASC, created_at DESC NULLS LAST").fetchall()]
    pending = sum(1 for r in rows if r["role"] != "admin" and not r["approved"])
    trs = ""
    for r in rows:
        vr = "✅" if r["email_verified"] else "⛔"
        if r["role"] == "admin":
            ap, act = "관리자", ""
        elif r["approved"]:
            ap = '<span style="color:#059669;font-weight:700">승인됨</span>'
            act = _form("auth.member_approve", r["id"], "승인취소", "approve", "0", "#64748b")
        else:
            ap = '<span style="color:#dc2626;font-weight:700">대기</span>'
            act = _form("auth.member_approve", r["id"], "✔ 승인", "approve", "1", "#4321F3")
        delbtn = _form("auth.member_delete", r["id"], "삭제", confirm=True) if r["role"] != "admin" else ""
        trs += (f"<tr><td>{r['id']}</td><td>{r.get('email') or '-'}</td><td>{r.get('name') or ''}</td>"
                f"<td>{r.get('birthdate') or ''}</td><td>{vr}</td><td>{ap}</td>"
                f"<td>{(r.get('created_at') or '')[:10]}</td>"
                f"<td style='white-space:nowrap'>{act} {delbtn}</td></tr>")
    note = (f'<div class="msg info">승인 대기 {pending}명</div>' if pending else "")
    body = f"""<h1>👥 회원 관리</h1><p class="sub">관리자: {u['username'] or u['email']} ·
      <a href="/">홈</a> · <a href="{url_for('auth.logout')}">로그아웃</a></p>{note}
    <div class="tw"><table><thead><tr><th>ID</th><th>이메일</th><th>이름</th><th>생년월일</th>
      <th>인증</th><th>승인</th><th>가입일</th><th>관리</th></tr></thead><tbody>{trs}</tbody></table></div>"""
    return _render("회원 관리", body, boxstyle="max-width:820px")


@bp.route("/api/online")
def api_online():
    """현재 접속자수(관리자 전용). 대시보드/크롤현황에서 폴링."""
    u = current_user()
    if not u or u["role"] != "admin":
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"online": online_count() or 0})


@bp.route("/crawl")
def crawl_status():
    """관리자 전용 크롤링 현황 대시보드.
    부동산(목록·상세)/렌트 테이블별 총 건수 + 마지막 크롤 시각 + 최근 일별 신규(증분) 추이.
    크롤이 매일/매주 정상적으로 돌며 데이터가 쌓이는지 모니터링하는 용도."""
    u = current_user()
    if not u or u["role"] != "admin":
        return redirect(url_for("auth.login", next=request.path))

    conn = db.connect()

    def scalar(sql):
        try:
            r = conn.execute(sql).fetchone()
            return r[0] if r else None
        except Exception:
            return None

    def rows(sql):
        try:
            return conn.execute(sql).fetchall()
        except Exception:
            return []

    try:
        n_list = scalar("SELECT COUNT(*) FROM listings") or 0
        n_naver = scalar("SELECT COUNT(*) FROM naver_listings") or 0
        n_samsam = scalar("SELECT COUNT(*) FROM samsam_listings") or 0
        last_list = scalar("SELECT MAX(crawled_at) FROM listings")
        last_naver = scalar("SELECT MAX(crawled_at) FROM naver_listings")
        last_samsam = scalar("SELECT MAX(collected_at) FROM samsam_listings")
        # collected_at/crawled_at 앞 10글자 = 날짜(YYYY-MM-DD). 신규 매물만 수집하므로 날짜별 건수 = 일별 증분.
        d_samsam = {r[0]: r[1] for r in rows(
            "SELECT substr(collected_at,1,10) d, COUNT(*) c FROM samsam_listings "
            "WHERE collected_at IS NOT NULL GROUP BY substr(collected_at,1,10) "
            "ORDER BY d DESC LIMIT 14")}
        d_naver = {r[0]: r[1] for r in rows(
            "SELECT substr(crawled_at,1,10) d, COUNT(*) c FROM naver_listings "
            "WHERE crawled_at IS NOT NULL GROUP BY substr(crawled_at,1,10) "
            "ORDER BY d DESC LIMIT 14")}
        snaps = rows(
            "SELECT snapshot_date d, COUNT(*) c, COALESCE(SUM(n),0) tot FROM samsam_snapshots "
            "GROUP BY snapshot_date ORDER BY snapshot_date DESC LIMIT 14")
    finally:
        conn.close()

    def card(title, n, last):
        return (f'<div style="background:#f9fafb;border:1px solid #eef0f2;border-radius:10px;padding:14px">'
                f'<div style="font-size:12px;color:#64748b">{title}</div>'
                f'<div style="font-size:22px;font-weight:800;color:#111827">{n:,}</div>'
                f'<div style="font-size:11px;color:#94a3b8;margin-top:2px">마지막: {last or "-"}</div></div>')

    cards = (f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:14px 0">'
             f'{card("부동산 목록 (listings)", n_list, last_list)}'
             f'{card("부동산 상세 (naver_listings)", n_naver, last_naver)}'
             f'{card("렌트 매물 (samsam_listings)", n_samsam, last_samsam)}</div>')

    dates = sorted(set(d_samsam) | set(d_naver), reverse=True)[:14]
    if dates:
        drows = "".join(
            f"<tr><td>{d}</td><td style='text-align:right'>{d_samsam.get(d,0):,}</td>"
            f"<td style='text-align:right'>{d_naver.get(d,0):,}</td></tr>" for d in dates)
        daily = (f'<h2 style="font-size:15px;margin:22px 0 6px">📈 일별 신규 매물 (최근 14일)</h2>'
                 f'<div class="tw"><table><thead><tr><th>날짜</th><th style="text-align:right">렌트 신규</th>'
                 f'<th style="text-align:right">부동산 상세 신규</th></tr></thead><tbody>{drows}</tbody></table></div>')
    else:
        daily = '<div class="msg info" style="margin-top:20px">아직 일별 수집 기록이 없습니다.</div>'

    if snaps:
        srows = "".join(
            f"<tr><td>{r[0]}</td><td style='text-align:right'>{r[1]:,}</td>"
            f"<td style='text-align:right'>{r[2]:,}</td></tr>" for r in snaps)
        snap_tbl = (f'<h2 style="font-size:15px;margin:22px 0 6px">🗓️ 렌트 예약률 스냅샷 이력 (최근 14회)</h2>'
                    f'<div class="tw"><table><thead><tr><th>스냅샷 날짜</th><th style="text-align:right">지역×유형 수</th>'
                    f'<th style="text-align:right">매물 합계</th></tr></thead><tbody>{srows}</tbody></table></div>')
    else:
        snap_tbl = ""

    churn = latest_listing_churn()
    online = online_count()

    def _churn_section(ch):
        if not ch or not ch["rows"]:
            return ('<div class="msg info" style="margin-top:20px">렌트 매물 추가/삭제 집계가 '
                    '아직 없습니다 (다음 렌트 크롤 후 표시).</div>')
        tr = ""
        for full, short in (("서울특별시", "서울"), ("경기도", "경기"), ("인천광역시", "인천")):
            c = ch["rows"].get(full, {"added": 0, "removed": 0, "total": 0})
            tr += (f"<tr><td>{short}</td>"
                   f"<td style='text-align:right;color:#059669;font-weight:700'>+{c['added']:,}</td>"
                   f"<td style='text-align:right;color:#dc2626;font-weight:700'>-{c['removed']:,}</td>"
                   f"<td style='text-align:right'>{c['total']:,}</td></tr>")
        return (f'<h2 style="font-size:15px;margin:22px 0 6px">🔄 렌트 매물 변동 (최근 크롤 {ch["date"]})</h2>'
                f'<div class="tw"><table><thead><tr><th>지역</th>'
                f'<th style="text-align:right">신규 추가</th><th style="text-align:right">삭제</th>'
                f'<th style="text-align:right">현재</th></tr></thead><tbody>{tr}</tbody></table></div>')

    online_txt = (online if online is not None else "-")
    body = (f'<h1>📊 크롤링 현황</h1>'
            f'<p class="sub">관리자: {u["username"] or u["email"]} · '
            f'👥 현재 접속 <b id="online">{online_txt}</b>명 · '
            f'<a href="/">홈</a> · <a href="{url_for("auth.members")}">회원 관리</a> · '
            f'<a href="{url_for("auth.logout")}">로그아웃</a></p>'
            f'<div class="msg info">부동산: 매주 월 10:00(KST) · 렌트: 매일 00:00(KST) 자동 크롤</div>'
            f'{cards}{_churn_section(churn)}{daily}{snap_tbl}'
            '<script>setInterval(function(){fetch("/auth/api/online")'
            '.then(function(r){return r.ok?r.json():null})'
            '.then(function(d){if(d&&d.online!=null){var e=document.getElementById("online");'
            'if(e)e.textContent=d.online}}).catch(function(){});},15000);</script>')
    return _render("크롤링 현황", body, boxstyle="max-width:820px")


def _form(endpoint, mid, label, extra_name=None, extra_val=None, color="#dc2626", confirm=False):
    extra = f'<input type=hidden name={extra_name} value="{extra_val}">' if extra_name else ""
    onclick = ' onclick="return confirm(\'삭제?\')"' if confirm else ""
    style = ("border:none;background:none;font-weight:700;cursor:pointer;"
             f"color:{color}")
    return (f'<form method=post action="{url_for(endpoint)}" style="display:inline;margin:0">'
            f'<input type=hidden name=id value="{mid}">{extra}'
            f'<button style="{style}"{onclick}>{label}</button></form>')


@bp.route("/members/approve", methods=["POST"])
def member_approve():
    u = current_user()
    if not u or u["role"] != "admin":
        return redirect(url_for("auth.login"))
    val = request.form.get("approve") == "1"
    conn = db.connect()
    conn.execute("UPDATE members SET approved=%s WHERE id=%s AND role<>'admin'",
                 (val, request.form.get("id")))
    conn.commit()
    return redirect(url_for("auth.members"))


@bp.route("/members/delete", methods=["POST"])
def member_delete():
    u = current_user()
    if not u or u["role"] != "admin":
        return redirect(url_for("auth.login"))
    conn = db.connect()
    conn.execute("DELETE FROM members WHERE id=%s AND role<>'admin'",
                 (request.form.get("id"),))
    conn.commit()
    return redirect(url_for("auth.members"))


@bp.route("/kakao")
def kakao_login():
    state = secrets.token_urlsafe(16)
    session["_kst"] = state
    redirect_uri = _KAKAO_REDIRECT_URI or url_for("auth.kakao_callback", _external=True)
    params = (f"client_id={_KAKAO_CLIENT_ID}"
              f"&redirect_uri={redirect_uri}"
              f"&response_type=code&state={state}")
    return redirect(f"https://kauth.kakao.com/oauth/authorize?{params}")


@bp.route("/kakao/callback")
def kakao_callback():
    if request.args.get("error") or request.args.get("state") != session.pop("_kst", None):
        return redirect(url_for("auth.login"))
    code = request.args.get("code", "")

    # 토큰 교환 (redirect_uri는 인가 요청과 동일해야 함)
    redirect_uri = _KAKAO_REDIRECT_URI or url_for("auth.kakao_callback", _external=True)
    data = {"grant_type": "authorization_code", "client_id": _KAKAO_CLIENT_ID,
            "redirect_uri": redirect_uri, "code": code}
    if _KAKAO_CLIENT_SECRET:
        data["client_secret"] = _KAKAO_CLIENT_SECRET
    tok = _requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=10)
    if tok.status_code != 200:
        return redirect(url_for("auth.login"))
    access_token = tok.json().get("access_token", "")
    if not access_token:
        return redirect(url_for("auth.login"))

    # 사용자 정보 조회
    me = _requests.get("https://kapi.kakao.com/v2/user/me",
                       headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
    if me.status_code != 200:
        return redirect(url_for("auth.login"))
    info = me.json()
    kakao_id = str(info.get("id") or "")
    ka = info.get("kakao_account") or {}
    email = (ka.get("email") or "").lower() or None
    name = ((ka.get("profile") or {}).get("nickname")
            or (info.get("properties") or {}).get("nickname")
            or "카카오회원")
    if not kakao_id:
        return redirect(url_for("auth.login"))

    conn = db.connect()
    pending = False
    try:
        row = conn.execute("SELECT id,role FROM members WHERE kakao_id=%s", (kakao_id,)).fetchone()
        if row and name and name != "카카오회원":
            # 로그인마다 닉네임 갱신(최초 '카카오회원'으로 저장된 경우 포함)
            conn.execute("UPDATE members SET name=%s WHERE id=%s", (name, row["id"]))
            conn.commit()
        if not row and email:
            # 이메일로 기존 계정에 kakao_id 연결
            row2 = conn.execute("SELECT id,role FROM members WHERE email=%s", (email,)).fetchone()
            if row2:
                conn.execute("UPDATE members SET kakao_id=%s WHERE id=%s", (kakao_id, row2["id"]))
                conn.commit()
                row = row2
        if not row:
            # 신규 회원 생성. 카카오 인증 = 이메일 인증 대체(email_verified=TRUE)이나,
            # 관리자 승인은 이메일 가입과 동일하게 대기(approved=FALSE) → 승인 후 로그인.
            conn.execute(
                "INSERT INTO members(email,password_hash,name,role,email_verified,approved,"
                "kakao_id,created_at) VALUES(%s,'!',%s,'member',TRUE,FALSE,%s,%s)",
                (email, name, kakao_id, _now().isoformat(timespec="seconds")))
            conn.commit()
            row = conn.execute("SELECT id,role FROM members WHERE kakao_id=%s",
                               (kakao_id,)).fetchone()
        if not row:
            return redirect(url_for("auth.login"))
        # 관리자 승인 게이트(이메일 로그인과 동일): 미승인 일반 회원은 로그인 불가.
        st = conn.execute("SELECT role,approved FROM members WHERE id=%s",
                          (row["id"],)).fetchone()
        if st["role"] != "admin" and not st["approved"]:
            pending = True
        else:
            session["uid"] = row["id"]
            session["role"] = row["role"]
            session.permanent = True
    finally:
        conn.close()
    if pending:
        body = (f'<h1>✅ 가입 완료</h1><p class="sub">{name}님, 카카오로 가입되었습니다.</p>'
                f'<div class="msg ok"><b>관리자 승인 후</b> 로그인할 수 있습니다. '
                f'승인되면 안내해 드립니다.</div>'
                f'<div class=lnk><a href="{url_for("auth.login")}">로그인 화면으로</a></div>')
        return _render("가입 완료", body)
    return redirect(request.args.get("next") or "/")


# 빈 #root 대체용 로딩 스켈레톤(첫 페인트에 nav와 함께 화면을 채움 → React가 교체).
_ROOT_SKELETON = (
    '<div id="root"><div style="min-height:60vh;display:flex;align-items:center;'
    "justify-content:center;color:#94a3b8;font:600 14px 'Malgun Gothic',sans-serif\">"
    '불러오는 중…</div></div>'
)


def init_auth(app, demo_endpoints=None):
    """앱에 로그인 게이트 적용. 모든 라우트를 보호하고 /auth/* 와 정적파일만 허용.
    demo_endpoints: 미로그인도 접근 허용할 endpoint 집합(데모 게이트 — 일부 공개해 회원가입 유도)."""
    demo_eps = set(demo_endpoints or ())
    app.secret_key = os.environ.get("SECRET_KEY") or "dev-insecure-change-me"
    app.permanent_session_lifetime = dt.timedelta(days=14)
    try:
        db.init_db()
    except Exception as e:
        print(f"[auth] init_db 경고: {repr(e)[:80]}", flush=True)
    app.register_blueprint(bp)

    @app.before_request
    def _guard():
        ep = request.endpoint or ""
        # 현재 접속자 핑: 로그인 세션의 실제 페이지/데이터 요청마다 최근 활동시각 갱신(정적파일 제외).
        if session.get("uid") and ep != "static":
            _ping_visitor()
        # chat_api_cron_poll: 외부 크론 서비스가 세션 없이 호출 — 자체 CRON_SECRET 검증으로 대체 보호.
        # home: 공개 랜딩(미로그인도 서비스 소개를 보게 — 로그인 창부터 뜨지 않도록).
        if (ep.startswith("auth.") or ep in ("static", "home", "chat_api_cron_poll")):
            return None
        if not session.get("uid"):
            if ep in demo_eps:   # 데모 허용 endpoint는 미로그인도 통과(제한된 데이터)
                return None
            return redirect(url_for("auth.login", next=request.path))
        return None

    @app.after_request
    def _inject_nav(resp):
        # 로그인 상태의 HTML 풀페이지 상단에 공통 네비게이션 바 주입(템플릿 수정 없이 전 뷰어 공통).
        try:
            ct = resp.content_type or ""
            if (session.get("uid") and ct.startswith("text/html")
                    and not (request.endpoint or "").startswith("auth.")):
                html = resp.get_data(as_text=True)
                i = html.find("<body")
                if i != -1 and "id=__nav" not in html:
                    gt = html.find(">", i)
                    if gt != -1:
                        html = html[:gt + 1] + _nav_html() + html[gt + 1:]
                        # 빈 #root 를 로딩 스켈레톤으로 채워 JS 로드 전에도 nav와 함께 화면이 차게 한다
                        # (React가 마운트되며 교체) → 'nav만 먼저 뜨는' 깜빡임 제거.
                        html = html.replace('<div id="root"></div>', _ROOT_SKELETON)
                        resp.set_data(html)
        except Exception:
            pass
        return resp

    return app


def _nav_html():
    admin = ('<a href="/auth/crawl">크롤 현황</a><a href="/auth/members">회원 관리</a>'
             if session.get("role") == "admin" else "")
    # 브랜드 로고 + 메뉴. 데스크탑은 가로 메뉴, 모바일은 햄버거(순수 CSS 체크박스 토글, JS 불필요).
    return f"""<input type=checkbox id=__navtog hidden>
<nav id=__nav>
  <div class=__inner>
    <a class=__brand href="/">ren<b>dit</b></a>
    <label for=__navtog class=__ham aria-label="메뉴">☰</label>
    <div class=__menu>
      <a href="/profit/">수익성</a>
      <a href="/samsam/">렌트 분석</a>
      <a href="/gangnam/">부동산매물</a>
      <a href="/samsam/chat/">통합채팅</a>
      {admin}
      <a href="/auth/logout" class=__logout>로그아웃</a>
    </div>
  </div>
</nav>
<style>
#__nav{{position:sticky;top:0;z-index:9999;background:#0f172a;color:#e2e8f0;
font-family:'Pretendard','Malgun Gothic',sans-serif;box-shadow:0 1px 6px rgba(0,0,0,.2)}}
/* 컨텐츠와 같은 폭으로 가운데 정렬 — 로고/메뉴가 화면 끝에 붙지 않게 */
#__nav .__inner{{max-width:1080px;margin:0 auto;width:100%;
display:flex;align-items:center;gap:8px;padding:14px 0}}
#__nav .__brand{{font-size:24px;font-weight:900;color:#fff;text-decoration:none;letter-spacing:-.02em;margin-right:auto}}
#__nav .__brand b{{color:#8b7dff;font-weight:900}}
#__nav .__menu{{display:flex;align-items:center;gap:4px}}
#__nav .__menu a{{color:#cbd5e1;text-decoration:none;padding:8px 14px;border-radius:8px;font-weight:600;font-size:15px;white-space:nowrap}}
#__nav .__menu a:hover{{background:#1e293b;color:#fff}}
#__nav .__menu .__logout{{color:#94a3b8}}
#__nav .__ham{{display:none;font-size:24px;color:#e2e8f0;cursor:pointer;padding:2px 8px;user-select:none}}
@media(max-width:640px){{
  #__nav .__inner{{padding:12px 18px}}
  #__nav .__ham{{display:block}}
  #__nav .__menu{{display:none;position:absolute;top:100%;left:0;right:0;background:#0f172a;
    flex-direction:column;align-items:stretch;padding:6px 10px 12px;gap:0;box-shadow:0 8px 20px rgba(0,0,0,.35)}}
  #__navtog:checked ~ #__nav .__menu{{display:flex}}
  #__nav .__menu a{{padding:12px 10px;border-bottom:1px solid #1e293b;font-size:15.5px}}
}}
</style>"""
