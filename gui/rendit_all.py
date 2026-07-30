# -*- coding: utf-8 -*-
"""
rendit 통합 크롤러 — 네이버부동산 + 삼삼엠투, 하나의 .exe

어느 PC에서든(가정용 한국 IP) 실행해 rendit 운영 DB(오라클 서버)에 바로 적재한다.
네이버·삼삼 모두 데이터센터 IP가 차단되므로 이렇게 "사람 PC에서 돌려 서버에 넣는"
구조가 표준 경로다. 필요한 것(크롬 엔진, 예약률30%+ 타겟 동 목록)은 실행 중 자동
다운로드하므로 exe 파일 하나만 있으면 된다.

저장 위치 3가지(공통):
  1) rendit 서버(권장) — SSH로 오라클에 터널을 뚫어 내부 pg에 직적재.
     서버 주소/SSH 계정/비밀번호만 넣으면 pg 비밀번호는 서버에서 자동으로 읽어온다.
  2) DB URL — 임의의 Postgres URL(예: 예전 Supabase)에 적재.
  3) 로컬 폴더 — 이 PC에 SQLite(naver.db / samsam.db)로 누적. DB 없이도 동작.

탭:
  [네이버 부동산] 지역(전국)·거래유형·매물 7종·수집량·상세수집·CSV. 로그인 불필요.
    "예약률 30%+ 동만" 체크 시 GitHub에서 최신 타겟 동 목록을 내려받아 그 동만 크롤
    (매일 02:00 운영 크롤과 동일한 범위).
  [삼삼엠투] 계정(1~2개)·매물 6종·계정당 하루 예약조회 한도. 크롤 후 예약률 스냅샷.

개발 실행:  python gui/rendit_all.py
자체 점검:  python gui/rendit_all.py --selftest      (임포트/경로만, 크롤 안 함)
브라우저:   python gui/rendit_all.py --browsertest   (크롬 탐지/설치/실행 확인)
"""
import glob
import importlib.util
import json
import os
import queue
import select
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from socketserver import BaseRequestHandler, ThreadingTCPServer


# ── 경로 ────────────────────────────────────────────────────────────────────────
def resource_base():
    """번들 리소스(pipeline/, common/, db.py, data/, deploy/)의 루트."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE = resource_base()
TUNNEL_PORT = 15434          # 운영 크롤(삼삼 15432·네이버 15433)과 안 겹치는 포트
DONGS_URL = ('https://raw.githubusercontent.com/gunho30811/STA/main/'
             'deploy/naver_target_dongs.txt')


def _state_dir():
    base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    d = os.path.join(base, 'RenditCrawler')
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


CONFIG_PATH = os.path.join(_state_dir(), 'config.json')

# 화면 라벨 ↔ 코드
NAVER_SIDOS = ['서울시', '경기도', '인천시', '부산시', '대전시', '대구시', '울산시', '세종시',
               '전남광주시', '강원도', '충청북도', '충청남도', '경상북도', '경상남도', '전북도', '제주도']
CAPITAL = ['서울시', '경기도', '인천시']
NAVER_TYPES = [('아파트', 'APT'), ('오피스텔', 'OPST'), ('빌라', 'VL'), ('원룸', 'OR'),
               ('단독/다가구', 'DDDGG'), ('상가', 'SG'), ('전원주택', 'JWJT')]
TRADE_TYPES = [('월세', 'B2'), ('전세', 'B1'), ('매매', 'A1')]
SAMSAM_TYPES = [('오피스텔', 'OFFICETEL'), ('아파트', 'APARTMENT'), ('연립빌라', 'VILLA'),
                ('단독주택', 'DETACHED'), ('원룸건물', 'STUDIO'), ('상가주택', 'MIXED_USE')]


def _add_paths():
    for p in (BASE, os.path.join(BASE, 'common'),
              os.path.join(BASE, 'pipeline', 'samsam'),
              os.path.join(BASE, 'pipeline', 'naver')):
        if p not in sys.path:
            sys.path.insert(0, p)


def _load_from_file(name, relpath):
    """레포 트리의 .py 를 경로에서 모듈로 로드(네이버/삼삼 crawler.py 이름충돌 회피)."""
    path = os.path.join(BASE, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class QueueWriter:
    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(s)

    def flush(self):
        pass

    def reconfigure(self, *a, **k):
        pass


# ── SSH 터널(오라클 내부 pg) — deploy/crawl_samsam_local.py 와 같은 방식 ─────────
def _make_handler(transport, remote_host, remote_port):
    class H(BaseRequestHandler):
        def handle(self):
            chan = None
            for attempt in range(3):
                try:
                    chan = transport.open_channel(
                        'direct-tcpip', (remote_host, remote_port), self.request.getpeername())
                    if chan is not None:
                        break
                except Exception as ex:
                    print(f"[터널] 채널 열기 실패({attempt + 1}/3): {ex}")
                time.sleep(0.5 * (attempt + 1))
            if chan is None:
                print("[터널] 채널 포기 — DB 연결 1건 실패")
                self.request.close()
                return
            try:
                while True:
                    r, _, _ = select.select([self.request, chan], [], [])
                    if self.request in r:
                        data = self.request.recv(4096)
                        if not data:
                            break
                        chan.sendall(data)
                    if chan in r:
                        data = chan.recv(4096)
                        if not data:
                            break
                        self.request.sendall(data)
            finally:
                chan.close()
                self.request.close()
    return H


class ServerTunnel:
    """SSH 접속 → pg 컨테이너 IP·비밀번호 자동 조회 → 로컬 포워딩. DATABASE_URL 제공."""

    def __init__(self, host, user, password):
        self.host, self.user, self.password = host, user, password
        self.ssh = None
        self.server = None
        self.db_url = None

    def open(self):
        import paramiko
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(self.host, username=self.user, password=self.password, timeout=20)
        c.get_transport().set_keepalive(15)
        self.ssh = c

        def run(cmd):
            _, o, _ = c.exec_command(cmd, timeout=30)
            return o.read().decode().strip()

        pg_ip = run("sudo docker inspect pg "
                    "--format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'")
        pw = run("grep '^LOCAL_PG_PASSWORD=' /home/ubuntu/STA/.env | cut -d= -f2-")
        if not pg_ip or not pw:
            raise RuntimeError(f"서버에서 pg 정보 조회 실패 (ip={pg_ip!r})")
        self.server = ThreadingTCPServer(('127.0.0.1', TUNNEL_PORT),
                                         _make_handler(c.get_transport(), pg_ip, 5432))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.db_url = ('postgresql://postgres:%s@127.0.0.1:%d/rendit?sslmode=disable'
                       % (urllib.parse.quote(pw, safe=''), TUNNEL_PORT))
        print(f"[터널] 연결됨: {self.host} → 내부 pg({pg_ip})")
        return self.db_url

    def close(self):
        try:
            if self.server:
                self.server.shutdown()
        except Exception:
            pass
        try:
            if self.ssh:
                self.ssh.close()
        except Exception:
            pass


# ── 크롬 엔진(네이버용) — 설치본 사용, 없으면 자동 다운로드 ───────────────────────
def _ensure_chromium():
    lap = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    bpath = os.path.join(lap, 'ms-playwright')
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = bpath
    hit = glob.glob(os.path.join(bpath, 'chromium_headless_shell-*', '**', 'chrome-headless-shell.exe'),
                    recursive=True) or \
        glob.glob(os.path.join(bpath, 'chromium-*', '**', 'chrome.exe'), recursive=True)
    if hit:
        print(f"[GUI] 설치된 크롬 엔진 사용: {os.path.relpath(hit[0], bpath).split(os.sep)[0]}")
        return
    print("[GUI] 크롬 엔진이 없어 자동 설치합니다 (최초 1회, 인터넷 필요·~150MB, 몇 분)...")
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        driver = compute_driver_executable()
        cmd = (list(driver) if isinstance(driver, (list, tuple)) else [driver]) + ['install', 'chromium']
        env = dict(get_driver_env())
        env['PLAYWRIGHT_BROWSERS_PATH'] = bpath
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace')
        for line in proc.stdout:
            if line.strip():
                print("  " + line.rstrip())
        proc.wait()
        print("[GUI] 크롬 엔진 설치 완료" if proc.returncode == 0
              else f"[GUI] ⚠ 크롬 설치 실패(code={proc.returncode}) — 인터넷 확인 후 다시 시도")
    except Exception as e:
        print(f"[GUI] ⚠ 크롬 자동설치 오류: {e}")


def _fetch_target_dongs():
    """예약률 30%+ 타겟 동 목록 — GitHub 최신본 다운로드, 실패 시 번들본 사용. 경로 반환."""
    dest = os.path.join(_state_dir(), 'naver_target_dongs.txt')
    try:
        with urllib.request.urlopen(DONGS_URL, timeout=15) as r:
            data = r.read()
        if len(data) > 100:                       # 비정상 응답(에러 페이지 등) 방지
            with open(dest, 'wb') as f:
                f.write(data)
            n = sum(1 for line in data.decode('utf-8', 'ignore').splitlines() if line.strip())
            print(f"[GUI] 타겟 동 목록 최신본 다운로드: {n}개 동")
            return dest
    except Exception as e:
        print(f"[GUI] 타겟 동 목록 다운로드 실패({e}) — 번들본 사용")
    bundled = os.path.join(BASE, 'deploy', 'naver_target_dongs.txt')
    if os.path.exists(dest):
        return dest
    return bundled


def _setup_backend(opts, kind):
    """저장 백엔드 준비. kind='naver'|'samsam'. (tunnel or None) 반환. sys.modules['db'] 세팅."""
    mode = opts['mode']
    tunnel = None
    if mode == 'server':
        print("[GUI] 저장 위치: rendit 서버(오라클) — SSH 터널 연결 중...")
        tunnel = ServerTunnel(opts['ssh_host'].strip(), opts['ssh_user'].strip(), opts['ssh_pw'])
        os.environ['DATABASE_URL'] = tunnel.open()
        db = _load_from_file('db', 'db.py')
        db.init_db()
    elif mode == 'dburl':
        url = opts['database_url'].strip()
        # pg8000+풀러(6543 트랜잭션모드) prepared statement 깨짐 방지 → 세션모드(5432)
        os.environ['DATABASE_URL'] = url.replace(':6543/', ':5432/')
        db = _load_from_file('db', 'db.py')
        print("[GUI] 저장 위치: DB URL(세션모드)")
        db.init_db()
    else:
        fname = 'naver.db' if kind == 'naver' else 'samsam.db'
        os.environ['SAMSAM_SQLITE_PATH'] = os.path.join(opts['folder'], fname)
        rel = os.path.join('common', 'naver_local_db.py' if kind == 'naver' else 'local_db.py')
        db = _load_from_file('db', rel)
        print(f"[GUI] 저장 위치: 로컬 폴더 → {os.environ['SAMSAM_SQLITE_PATH']}")
        db.init_db()
    return tunnel


# ── 네이버 파이프라인(워커 스레드) ───────────────────────────────────────────────
def run_naver(opts, log_q, stop_flag):
    _add_paths()
    for m in ('db', 'crawler', 'crawl_detail', 'detail_map', 'export_jsonl', 'subway'):
        sys.modules.pop(m, None)
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = QueueWriter(log_q)
    tunnel = None
    try:
        _ensure_chromium()
        tunnel = _setup_backend(opts, 'naver')

        dongs_file = None
        if opts.get('target_dongs'):
            dongs_file = _fetch_target_dongs()

        crawler = _load_from_file('crawler', os.path.join('pipeline', 'naver', 'crawler.py'))
        argv = ['crawler',
                '--sidos', ','.join(opts['sidos']),
                '--types', ','.join(opts['types']),
                '--trade-types', ','.join(opts['trades'])]
        if dongs_file:
            argv += ['--dongs-file', dongs_file]
        if opts.get('sample'):
            argv += ['--max-per-type', str(opts.get('test_count', 10))]
        print(f"[GUI] 네이버 크롤 시작 — 지역 {opts['sidos']} / 거래 {opts['trades']} / "
              f"종류 {len(opts['types'])}개"
              + ("  (예약률30%+ 동만)" if dongs_file else "")
              + (f"  (각 종류 {opts.get('test_count', 10)}개씩)" if opts.get('sample') else "  (전체)"))
        if stop_flag.is_set():
            return
        sys.argv = argv
        crawler.main()

        if stop_flag.is_set():
            print("[GUI] 중지 요청 — 후처리 생략")
            return

        if opts['detail']:
            print("[GUI] ── 상세정보 수집 ──")
            subway = _load_from_file('subway', os.path.join('common', 'subway.py'))
            if getattr(sys, 'frozen', False):
                subway._DATA = os.path.join(BASE, 'data', 'subway_stations.csv')
            _load_from_file('detail_map', os.path.join('pipeline', 'naver', 'detail_map.py'))
            cd = _load_from_file('crawl_detail', os.path.join('pipeline', 'naver', 'crawl_detail.py'))
            sys.argv = ['crawl_detail', '--sidos', ','.join(opts['sidos'])]
            try:
                cd.main()
            except Exception as e:
                print(f"[GUI] 상세 스킵({e})")

        # 서버 모드면 실시간 뷰 갱신 → 사이트에 바로 반영
        if opts['mode'] == 'server':
            print("[GUI] ── 사이트 실시간 뷰(nl_live) 갱신 ──")
            try:
                clv = _load_from_file('create_live_view',
                                      os.path.join('pipeline', 'naver', 'create_live_view.py'))
                sys.argv = ['create_live_view']
                clv.main()
            except Exception as e:
                print(f"[GUI] 뷰 갱신 스킵({e}) — 다음 운영 크롤이 보완")

        if opts['mode'] == 'local' and opts.get('csv'):
            try:
                out, n = _export_csv(os.environ['SAMSAM_SQLITE_PATH'], opts['folder'], 'naver_매물.csv')
                print(f"[GUI] 📄 CSV 저장: {out}  ({n:,}건)")
            except Exception as e:
                print(f"[GUI] CSV 저장 실패: {e}")

        print("[GUI] ✅ 네이버 전체 완료")
    except Exception:
        print("[GUI] ❌ 오류:\n" + traceback.format_exc())
    finally:
        if tunnel:
            tunnel.close()
        sys.stdout, sys.stderr = old_out, old_err
        log_q.put(('__DONE__',))


# ── 삼삼 파이프라인(워커 스레드) ─────────────────────────────────────────────────
def run_samsam(opts, log_q, stop_flag):
    _add_paths()
    for m in ('db', 'crawler', 'snapshot', 'export_jsonl', 'subway'):
        sys.modules.pop(m, None)
    os.environ['SAMSAM_EMAIL'] = opts['email1'].strip()
    os.environ['SAMSAM_PASSWORD'] = opts['password1']
    for k in ('SAMSAM_EMAIL2', 'SAMSAM_PASSWORD2'):
        os.environ.pop(k, None)
    if opts['email2'].strip():
        os.environ['SAMSAM_EMAIL2'] = opts['email2'].strip()
        os.environ['SAMSAM_PASSWORD2'] = opts['password2']
    # 계정당 하루 예약조회 한도(~5,000 실측) — 넘기면 소프트차단으로 이후 크롤까지 망가짐.
    os.environ['SAMSAM_REFRESH_DAILY_LIMIT'] = str(opts.get('daily_limit', 4000))
    os.environ['SAMSAM_BOOKED_COOLDOWN_DAYS'] = '0'

    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = QueueWriter(log_q)
    tunnel = None
    try:
        tunnel = _setup_backend(opts, 'samsam')

        subway = _load_from_file('subway', os.path.join('common', 'subway.py'))
        if getattr(sys, 'frozen', False):
            subway._DATA = os.path.join(BASE, 'data', 'subway_stations.csv')

        crawler = _load_from_file('crawler', os.path.join('pipeline', 'samsam', 'crawler.py'))
        sel = list(opts['types'])
        print(f"[GUI] 삼삼 크롤 시작 — 종류 {len(sel)}개: {', '.join(sel)}"
              + ("  (테스트: 각 50건)" if opts.get('test') else "")
              + f"  / 계정당 하루 한도 {opts.get('daily_limit', 4000)}건")
        if stop_flag.is_set():
            return
        sys.argv = ['crawler', '--types', ','.join(sel)] + \
            (['--limit', '50'] if opts.get('test') else [])
        crawler.main()

        if stop_flag.is_set():
            print("[GUI] 중지 요청 — 스냅샷 생략")
            return

        print("[GUI] ── 예약률 스냅샷 ──")
        snap = _load_from_file('snapshot', os.path.join('pipeline', 'samsam', 'snapshot.py'))
        sys.argv = ['snapshot']
        try:
            snap.main()
        except Exception as e:
            print(f"[GUI] 스냅샷 스킵({e})")

        print("[GUI] ✅ 삼삼 전체 완료")
    except Exception:
        print("[GUI] ❌ 오류:\n" + traceback.format_exc())
    finally:
        if tunnel:
            tunnel.close()
        sys.stdout, sys.stderr = old_out, old_err
        log_q.put(('__DONE__',))


def _export_csv(dbpath, folder, fname):
    import csv
    import sqlite3
    out = os.path.join(folder, fname)
    conn = sqlite3.connect(dbpath)
    try:
        cur = conn.execute("SELECT * FROM listings ORDER BY sido, sigungu, dong")
        cols = [d[0] for d in cur.description]
        n = 0
        with open(out, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(cols)
            for row in cur:
                w.writerow(row)
                n += 1
    finally:
        conn.close()
    return out, n


# ── 자체 점검 ───────────────────────────────────────────────────────────────────
def selftest():
    _add_paths()
    os.environ.setdefault('DATABASE_URL', 'postg://selftest')
    os.environ.setdefault('SAMSAM_SQLITE_PATH', os.path.join(_state_dir(), '_selftest.db'))
    lines, ok = [], True
    for name, rel in [('subway', 'common/subway.py'),
                      ('db_pg', 'db.py'),
                      ('local_db', 'common/local_db.py'),
                      ('naver_local_db', 'common/naver_local_db.py'),
                      ('crawler_naver', 'pipeline/naver/crawler.py'),
                      ('detail_map', 'pipeline/naver/detail_map.py'),
                      ('crawl_detail', 'pipeline/naver/crawl_detail.py'),
                      ('create_live_view', 'pipeline/naver/create_live_view.py'),
                      ('crawler_samsam', 'pipeline/samsam/crawler.py'),
                      ('snapshot', 'pipeline/samsam/snapshot.py')]:
        try:
            _load_from_file(name, rel)
            lines.append(f"  OK  {rel}")
        except Exception as e:
            ok = False
            lines.append(f"  FAIL {rel}: {e}")
    for mod in ('tkinter', 'sqlite3', 'pg8000', 'paramiko', 'requests', 'dotenv',
                'playwright.sync_api'):
        try:
            __import__(mod)
            lines.append(f"  OK  import {mod}")
        except Exception as e:
            ok = False
            lines.append(f"  FAIL import {mod}: {e}")
    # 번들 데이터 파일
    for rel in (os.path.join('data', 'subway_stations.csv'),
                os.path.join('deploy', 'naver_target_dongs.txt')):
        p = os.path.join(BASE, rel)
        if os.path.exists(p):
            lines.append(f"  OK  bundle {rel}")
        else:
            ok = False
            lines.append(f"  FAIL bundle {rel} 없음")
    lines.append("SELFTEST " + ("PASS" if ok else "FAIL"))
    text = "\n".join(lines)
    print(text)
    try:
        with open(os.path.join(_state_dir(), 'selftest_result.txt'), 'w', encoding='utf-8') as f:
            f.write(text + "\n")
    except Exception:
        pass
    return 0 if ok else 1


def browsertest():
    _add_paths()
    _ensure_chromium()
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        b = pw.chromium.launch(headless=True, args=['--no-sandbox'])
        b.close()
        pw.stop()
        r = f"BROWSERTEST PASS (path={os.environ.get('PLAYWRIGHT_BROWSERS_PATH')})"
    except Exception as e:
        r = "BROWSERTEST FAIL: " + repr(e)[:300]
    print(r)
    try:
        with open(os.path.join(_state_dir(), 'browsertest_result.txt'), 'w', encoding='utf-8') as f:
            f.write(r + "\n")
    except Exception:
        pass
    return 0 if 'PASS' in r else 1


# ── GUI ─────────────────────────────────────────────────────────────────────────
def launch_gui():
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox, filedialog

    cfg = {}
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

    root = tk.Tk()
    root.title('rendit 통합 크롤러 — 네이버부동산 · 삼삼엠투')
    root.geometry('820x760')

    log_q = queue.Queue()
    stop_flag = threading.Event()
    worker = {'thread': None}

    # ── 공통: 저장 위치 ──
    sf = ttk.LabelFrame(root, text='저장 위치 (공통)', padding=8)
    sf.pack(fill='x', padx=10, pady=(10, 4))
    mode_var = tk.StringVar(value=cfg.get('mode', 'server'))
    mrow = ttk.Frame(sf)
    mrow.grid(row=0, column=0, columnspan=4, sticky='w')
    ttk.Radiobutton(mrow, text='rendit 서버 (권장 — 사이트에 바로 반영)', value='server',
                    variable=mode_var, command=lambda: sync_mode()).pack(side='left')
    ttk.Radiobutton(mrow, text='DB URL', value='dburl',
                    variable=mode_var, command=lambda: sync_mode()).pack(side='left', padx=10)
    ttk.Radiobutton(mrow, text='로컬 폴더 (SQLite)', value='local',
                    variable=mode_var, command=lambda: sync_mode()).pack(side='left')

    # 서버 모드 필드
    lbl_host = ttk.Label(sf, text='서버 주소', width=11)
    e_host = ttk.Entry(sf, width=34)
    e_host.insert(0, cfg.get('ssh_host', 'rendits.duckdns.org'))
    lbl_user = ttk.Label(sf, text='SSH 계정')
    e_user = ttk.Entry(sf, width=12)
    e_user.insert(0, cfg.get('ssh_user', 'ubuntu'))
    lbl_spw = ttk.Label(sf, text='SSH 비밀번호')
    e_spw = ttk.Entry(sf, width=20, show='*')
    e_spw.insert(0, cfg.get('ssh_pw', ''))
    # DB URL 모드 필드
    lbl_db = ttk.Label(sf, text='DB URL', width=11)
    e_db = ttk.Entry(sf, width=76)
    e_db.insert(0, cfg.get('database_url', ''))
    # 로컬 모드 필드
    lbl_folder = ttk.Label(sf, text='저장 폴더', width=11)
    e_folder = ttk.Entry(sf, width=62)
    e_folder.insert(0, cfg.get('folder', ''))

    def browse():
        d = filedialog.askdirectory(title='데이터를 쌓을 폴더 선택')
        if d:
            e_folder.delete(0, 'end')
            e_folder.insert(0, d)

    btn_browse = ttk.Button(sf, text='찾아보기', command=browse)

    def sync_mode():
        for w in (lbl_host, e_host, lbl_user, e_user, lbl_spw, e_spw,
                  lbl_db, e_db, lbl_folder, e_folder, btn_browse):
            w.grid_remove()
        m = mode_var.get()
        if m == 'server':
            lbl_host.grid(row=1, column=0, sticky='w', pady=2)
            e_host.grid(row=1, column=1, sticky='w', pady=2)
            lbl_user.grid(row=1, column=2, sticky='e', padx=(10, 2))
            e_user.grid(row=1, column=3, sticky='w')
            lbl_spw.grid(row=2, column=0, sticky='w', pady=2)
            e_spw.grid(row=2, column=1, sticky='w', pady=2)
        elif m == 'dburl':
            lbl_db.grid(row=1, column=0, sticky='w', pady=2)
            e_db.grid(row=1, column=1, columnspan=3, sticky='we', pady=2)
        else:
            lbl_folder.grid(row=1, column=0, sticky='w', pady=2)
            e_folder.grid(row=1, column=1, columnspan=2, sticky='we', pady=2)
            btn_browse.grid(row=1, column=3, padx=4)

    sync_mode()

    # ── 탭 ──
    nb = ttk.Notebook(root)
    nb.pack(fill='x', padx=10, pady=4)

    # [네이버 탭]
    tab_n = ttk.Frame(nb, padding=8)
    nb.add(tab_n, text='  네이버 부동산  ')

    rf = ttk.LabelFrame(tab_n, text='지역 (복수 선택)', padding=6)
    rf.pack(fill='x')
    saved_sidos = set(cfg.get('sidos', CAPITAL))
    sido_vars = {}
    PER_ROW = 6
    for i, s in enumerate(NAVER_SIDOS):
        v = tk.BooleanVar(value=(s in saved_sidos))
        ttk.Checkbutton(rf, text=s, variable=v).grid(
            row=i // PER_ROW, column=i % PER_ROW, padx=5, pady=1, sticky='w')
        sido_vars[s] = v
    brow = ttk.Frame(rf)
    brow.grid(row=(len(NAVER_SIDOS) // PER_ROW) + 1, column=0, columnspan=PER_ROW,
              sticky='w', pady=(4, 0))

    def _set_sidos(names):
        for s, v in sido_vars.items():
            v.set(s in names)

    ttk.Button(brow, text='전국 전체', width=9, command=lambda: _set_sidos(NAVER_SIDOS)).pack(side='left')
    ttk.Button(brow, text='수도권만', width=9, command=lambda: _set_sidos(CAPITAL)).pack(side='left', padx=4)
    ttk.Button(brow, text='전체 해제', width=9, command=lambda: _set_sidos([])).pack(side='left')

    row2 = ttk.Frame(tab_n)
    row2.pack(fill='x', pady=(4, 0))
    trf = ttk.LabelFrame(row2, text='거래유형', padding=6)
    trf.pack(side='left')
    saved_trades = set(cfg.get('trades', ['B2']))
    trade_vars = {}
    for i, (label, code) in enumerate(TRADE_TYPES):
        v = tk.BooleanVar(value=(code in saved_trades))
        ttk.Checkbutton(trf, text=label, variable=v).grid(row=0, column=i, padx=6)
        trade_vars[code] = v
    qf = ttk.LabelFrame(row2, text='수집량', padding=6)
    qf.pack(side='left', padx=8)
    limit_mode = tk.StringVar(value=cfg.get('limit_mode', 'all'))
    ttk.Radiobutton(qf, text='전체', value='all', variable=limit_mode).pack(side='left')
    ttk.Radiobutton(qf, text='각 종류', value='sample', variable=limit_mode).pack(side='left', padx=(10, 2))
    test_count = ttk.Spinbox(qf, from_=1, to=1000000, width=7)
    test_count.set(str(cfg.get('test_count', 10)))
    test_count.pack(side='left')
    ttk.Label(qf, text='개씩').pack(side='left', padx=(2, 0))

    tf = ttk.LabelFrame(tab_n, text='매물 종류 (복수 선택)', padding=6)
    tf.pack(fill='x', pady=(4, 0))
    saved_ntypes = set(cfg.get('ntypes', [c for _, c in NAVER_TYPES]))
    ntype_vars = {}
    for i, (label, code) in enumerate(NAVER_TYPES):
        v = tk.BooleanVar(value=(code in saved_ntypes))
        ttk.Checkbutton(tf, text=label, variable=v).grid(row=0, column=i, padx=5)
        ntype_vars[code] = v

    onf = ttk.Frame(tab_n)
    onf.pack(fill='x', pady=(4, 0))
    dongs_var = tk.BooleanVar(value=cfg.get('target_dongs', False))
    ttk.Checkbutton(onf, text='예약률 30%+ 동만 (운영 크롤과 동일 · 목록 자동 다운로드)',
                    variable=dongs_var).pack(side='left')
    detail_var = tk.BooleanVar(value=cfg.get('detail', False))
    ttk.Checkbutton(onf, text='상세정보도 수집(느림)', variable=detail_var).pack(side='left', padx=10)
    ncsv_var = tk.BooleanVar(value=cfg.get('csv', True))
    ttk.Checkbutton(onf, text='CSV 저장(로컬 모드)', variable=ncsv_var).pack(side='left')

    bfn = ttk.Frame(tab_n)
    bfn.pack(fill='x', pady=(6, 0))
    btn_naver = ttk.Button(bfn, text='▶ 네이버 크롤 시작')
    btn_naver.pack(side='left')

    # [삼삼 탭]
    tab_s = ttk.Frame(nb, padding=8)
    nb.add(tab_s, text='  삼삼엠투  ')

    af = ttk.LabelFrame(tab_s, text='삼삼 계정 (예약률 조회용 · 2개면 커버리지 2배)', padding=6)
    af.pack(fill='x')

    def acc_row(parent, label, r, show=None, default=''):
        ttk.Label(parent, text=label, width=12).grid(row=r, column=0, sticky='w', pady=2)
        e = ttk.Entry(parent, width=40, show=show)
        e.grid(row=r, column=1, sticky='w', pady=2)
        e.insert(0, default)
        return e

    e_em1 = acc_row(af, '이메일 1', 0, default=cfg.get('email1', ''))
    e_pw1 = acc_row(af, '비밀번호 1', 1, show='*', default=cfg.get('password1', ''))
    e_em2 = acc_row(af, '이메일 2', 2, default=cfg.get('email2', ''))
    e_pw2 = acc_row(af, '비밀번호 2', 3, show='*', default=cfg.get('password2', ''))

    stf = ttk.LabelFrame(tab_s, text='매물 종류 (복수 선택)', padding=6)
    stf.pack(fill='x', pady=(4, 0))
    saved_stypes = set(cfg.get('stypes', ['OFFICETEL']))
    stype_vars = {}
    for i, (label, code) in enumerate(SAMSAM_TYPES):
        v = tk.BooleanVar(value=(code in saved_stypes))
        ttk.Checkbutton(stf, text=label, variable=v).grid(row=0, column=i, padx=5)
        stype_vars[code] = v

    osf = ttk.Frame(tab_s)
    osf.pack(fill='x', pady=(4, 0))
    stest_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(osf, text='테스트 (각 종류 50건만)', variable=stest_var).pack(side='left')
    ttk.Label(osf, text='   계정당 하루 예약조회 한도').pack(side='left')
    limit_spin = ttk.Spinbox(osf, from_=100, to=5000, increment=100, width=7)
    limit_spin.set(str(cfg.get('daily_limit', 4000)))
    limit_spin.pack(side='left', padx=2)
    ttk.Label(osf, text='건 (초과 시 계정 차단 위험)').pack(side='left')

    bfs = ttk.Frame(tab_s)
    bfs.pack(fill='x', pady=(6, 0))
    btn_samsam = ttk.Button(bfs, text='▶ 삼삼 크롤 시작')
    btn_samsam.pack(side='left')

    # ── 하단: 상태/중지/로그 ──
    cf = ttk.Frame(root, padding=(10, 2))
    cf.pack(fill='x')
    btn_stop = ttk.Button(cf, text='■ 중지', state='disabled')
    btn_stop.pack(side='left')
    save_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(cf, text='입력값 이 PC에 저장', variable=save_var).pack(side='left', padx=10)
    status = ttk.Label(cf, text='대기 중')
    status.pack(side='left', padx=8)

    log = scrolledtext.ScrolledText(root, height=16, font=('Consolas', 9))
    log.pack(fill='both', expand=True, padx=10, pady=(4, 10))

    def append(s):
        log.insert('end', s)
        log.see('end')

    def poll_queue():
        try:
            while True:
                item = log_q.get_nowait()
                if isinstance(item, tuple) and item and item[0] == '__DONE__':
                    btn_naver.config(state='normal')
                    btn_samsam.config(state='normal')
                    btn_stop.config(state='disabled')
                    status.config(text='완료' if not stop_flag.is_set() else '중지됨')
                    worker['thread'] = None
                else:
                    append(item)
        except queue.Empty:
            pass
        root.after(150, poll_queue)

    def _common_opts():
        return {
            'mode': mode_var.get(),
            'ssh_host': e_host.get(), 'ssh_user': e_user.get(), 'ssh_pw': e_spw.get(),
            'database_url': e_db.get(), 'folder': e_folder.get().strip(),
        }

    def _validate_common(o):
        if o['mode'] == 'server' and (not o['ssh_host'].strip() or not o['ssh_pw']):
            messagebox.showwarning('입력 필요', '서버 주소와 SSH 비밀번호를 입력하세요.')
            return False
        if o['mode'] == 'dburl' and not o['database_url'].strip():
            messagebox.showwarning('입력 필요', 'DB URL을 입력하세요.')
            return False
        if o['mode'] == 'local':
            if not o['folder']:
                messagebox.showwarning('입력 필요', '데이터를 쌓을 폴더를 선택하세요.')
                return False
            if not os.path.isdir(o['folder']):
                messagebox.showwarning('폴더 없음', f"폴더가 존재하지 않습니다:\n{o['folder']}")
                return False
        return True

    def _save_cfg(extra):
        if not save_var.get():
            return
        o = _common_opts()
        data = {**cfg, **{
            'mode': o['mode'], 'ssh_host': o['ssh_host'], 'ssh_user': o['ssh_user'],
            'ssh_pw': o['ssh_pw'], 'database_url': o['database_url'], 'folder': o['folder'],
        }, **extra}
        cfg.update(data)
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            append(f"[설정 저장 실패: {e}]\n")

    def _launch(target, opts, label):
        log.delete('1.0', 'end')
        stop_flag.clear()
        btn_naver.config(state='disabled')
        btn_samsam.config(state='disabled')
        btn_stop.config(state='normal')
        status.config(text=f'{label} 크롤 중…')
        t = threading.Thread(target=target, args=(opts, log_q, stop_flag), daemon=True)
        worker['thread'] = t
        t.start()

    def start_naver():
        o = _common_opts()
        if not _validate_common(o):
            return
        sidos = [s for s, v in sido_vars.items() if v.get()]
        trades = [c for c, v in trade_vars.items() if v.get()]
        types = [c for c, v in ntype_vars.items() if v.get()]
        if not sidos or not trades or not types:
            messagebox.showwarning('입력 필요', '지역·거래유형·매물종류를 각각 하나 이상 선택하세요.')
            return
        try:
            tcount = max(1, int(test_count.get()))
        except (ValueError, tk.TclError):
            tcount = 10
        o.update({'sidos': sidos, 'trades': trades, 'types': types,
                  'sample': (limit_mode.get() == 'sample'), 'test_count': tcount,
                  'target_dongs': dongs_var.get(), 'detail': detail_var.get(),
                  'csv': ncsv_var.get()})
        _save_cfg({'sidos': sidos, 'trades': trades, 'ntypes': types,
                   'limit_mode': limit_mode.get(), 'test_count': tcount,
                   'target_dongs': dongs_var.get(), 'detail': detail_var.get(),
                   'csv': ncsv_var.get()})
        _launch(run_naver, o, '네이버')

    def start_samsam():
        o = _common_opts()
        if not _validate_common(o):
            return
        if not e_em1.get().strip() or not e_pw1.get():
            messagebox.showwarning('입력 필요', '삼삼 이메일 1, 비밀번호 1은 필수입니다.')
            return
        types = [c for c, v in stype_vars.items() if v.get()]
        if not types:
            messagebox.showwarning('입력 필요', '매물 종류를 하나 이상 선택하세요.')
            return
        try:
            dlimit = max(100, min(5000, int(limit_spin.get())))
        except (ValueError, tk.TclError):
            dlimit = 4000
        o.update({'email1': e_em1.get(), 'password1': e_pw1.get(),
                  'email2': e_em2.get(), 'password2': e_pw2.get(),
                  'types': types, 'test': stest_var.get(), 'daily_limit': dlimit})
        _save_cfg({'email1': e_em1.get(), 'password1': e_pw1.get(),
                   'email2': e_em2.get(), 'password2': e_pw2.get(),
                   'stypes': types, 'daily_limit': dlimit})
        _launch(run_samsam, o, '삼삼')

    def stop():
        stop_flag.set()
        mod = sys.modules.get('crawler')
        if mod is not None and hasattr(mod, 'STOP'):
            mod.STOP = True
        status.config(text='중지 중…')
        append('\n[⏹ 중지 — 현재 작업을 마치면 멈춥니다]\n')

    btn_naver.config(command=start_naver)
    btn_samsam.config(command=start_samsam)
    btn_stop.config(command=stop)

    def on_close():
        if worker['thread'] and worker['thread'].is_alive():
            if not messagebox.askyesno('종료', '크롤이 진행 중입니다. 정말 종료할까요?'):
                return
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', on_close)
    append('저장 위치를 확인하고 탭에서 크롤을 시작하세요.\n'
           '· rendit 서버 모드: SSH 비밀번호만 넣으면 사이트 DB에 바로 적재됩니다.\n'
           '· 네이버는 로그인 불필요, 삼삼은 삼삼 계정이 필요합니다.\n'
           '· 필요한 크롬 엔진·타겟 동 목록은 자동으로 다운로드됩니다.\n\n')
    root.after(150, poll_queue)
    root.mainloop()


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    if '--browsertest' in sys.argv:
        sys.exit(browsertest())
    launch_gui()
