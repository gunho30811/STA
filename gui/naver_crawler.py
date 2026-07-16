# -*- coding: utf-8 -*-
"""
네이버 부동산 크롤러 — GUI (.exe 배포용). 삼삼 크롤러와 별개의 독립 프로그램.

버튼 하나로 로컬 PC에서 네이버부동산(new.land.naver.com) 매물을 크롤해
로컬 폴더(naver.db, SQLite)에 누적하거나 Supabase에 적재한다. 로그인 불필요
(Playwright로 공개 페이지 접근).

선택 항목:
  - 지역(수도권): 서울시 / 경기도 / 인천시 (복수 선택)
  - 거래유형: 월세 / 전세 / 매매 (복수 선택)
  - 매물종류: 아파트/오피스텔/빌라/원룸/단독다가구/상가/전원주택 (복수 선택)
  - 저장 위치: 로컬 폴더(naver.db) 또는 Supabase(DB URL)
  - 테스트(동 N개만) · 상세정보도 수집(느림) · JSONL export

동작: crawler.py(목록) → [선택]crawl_detail.py(상세) → [선택]export_jsonl.py
진행 로그가 창에 실시간으로 흐른다.

개발 실행:  python gui/naver_crawler.py
자체 점검:  python gui/naver_crawler.py --selftest
"""
import glob
import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
import traceback


def resource_base():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE = resource_base()
CONFIG_PATH = os.path.join(app_dir(), 'naver_crawler_config.json')

# 전국 시/도 (crawler.ALL_ROOTS 와 동일 이름). 기본은 수도권만 체크.
SIDOS = ['서울시', '경기도', '인천시', '부산시', '대전시', '대구시', '울산시', '세종시',
         '전남광주시', '강원도', '충청북도', '충청남도', '경상북도', '경상남도', '전북도', '제주도']
CAPITAL = ['서울시', '경기도', '인천시']
# 화면 라벨 → 네이버 realEstateType 코드
PROPERTY_TYPES = [
    ('아파트', 'APT'), ('오피스텔', 'OPST'), ('빌라', 'VL'), ('원룸', 'OR'),
    ('단독/다가구', 'DDDGG'), ('상가', 'SG'), ('전원주택', 'JWJT'),
]
# 화면 라벨 → 네이버 tradeType 코드
TRADE_TYPES = [('월세', 'B2'), ('전세', 'B1'), ('매매', 'A1')]


def _add_paths():
    for p in (BASE, os.path.join(BASE, 'common'), os.path.join(BASE, 'pipeline', 'naver')):
        if p not in sys.path:
            sys.path.insert(0, p)


def _load_from_file(name, relpath):
    """레포 트리의 .py 를 실제 경로에서 모듈로 로드(sys.modules 등록)."""
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


def _ensure_chromium():
    """번들 playwright가 exe 임시폴더가 아니라 'PC에 설치된' 크롬을 쓰게 하고,
    없으면 최초 1회 자동 다운로드. (사용자가 따로 playwright install 안 해도 되게.)"""
    lap = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    bpath = os.path.join(lap, 'ms-playwright')
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = bpath   # 번들 기본값(_MEI 로컬) 대신 시스템 위치
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
        env = dict(get_driver_env()); env['PLAYWRIGHT_BROWSERS_PATH'] = bpath
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


def _export_csv(dbpath, folder):
    """로컬 naver.db 의 listings 를 CSV(엑셀용, UTF-8 BOM)로 내보낸다. (경로, 건수) 반환."""
    import csv
    import sqlite3
    out = os.path.join(folder, 'naver_매물.csv')
    conn = sqlite3.connect(dbpath)
    try:
        cur = conn.execute("SELECT * FROM listings ORDER BY sido, sigungu, dong, realEstateType, tradeType")
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


def run_pipeline(opts, log_q, stop_flag):
    """워커 스레드: env 세팅 → crawler(목록) → [상세] → [export]. 로그는 log_q 로."""
    _add_paths()
    for m in ('db', 'crawler', 'crawl_detail', 'detail_map', 'export_jsonl', 'subway'):
        sys.modules.pop(m, None)

    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = QueueWriter(log_q)
    try:
        _ensure_chromium()   # PC 설치 크롬을 찾아 쓰거나, 없으면 자동 설치
        # 1) 저장 백엔드
        if opts['mode'] == 'local':
            os.environ['SAMSAM_SQLITE_PATH'] = os.path.join(opts['folder'], 'naver.db')
            db = _load_from_file('db', os.path.join('common', 'naver_local_db.py'))
            print(f"[GUI] 저장 위치: 로컬 폴더 → {os.environ['SAMSAM_SQLITE_PATH']}")
            db.init_db()
        else:
            url = opts['database_url'].strip()
            # pg8000+풀러(6543 트랜잭션모드)에서 prepared statement 깨짐 방지: 세션모드(5432)로.
            os.environ['DATABASE_URL'] = url.replace(':6543/', ':5432/')
            db = _load_from_file('db', 'db.py')
            print("[GUI] 저장 위치: Supabase(DB URL, 세션모드)")
            db.init_db()

        # 2) 목록 크롤
        crawler = _load_from_file('crawler', os.path.join('pipeline', 'naver', 'crawler.py'))
        argv = ['crawler',
                '--sidos', ','.join(opts['sidos']),
                '--types', ','.join(opts['types']),
                '--trade-types', ','.join(opts['trades'])]
        if opts.get('sample'):
            argv += ['--max-per-type', str(opts.get('test_count', 10))]
        print(f"[GUI] 목록 크롤 시작 — 지역 {opts['sidos']} / 거래 {opts['trades']} / 종류 {len(opts['types'])}개"
              + (f"  (각 종류 {opts.get('test_count', 10)}개씩)" if opts.get('sample') else "  (전체)"))
        if stop_flag.is_set():
            return
        sys.argv = argv
        crawler.main()

        if stop_flag.is_set():
            print("[GUI] 중지 요청 — 상세/export 생략")
            return

        # 3) 상세(선택) — subway/detail_map 의존
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

        # 4) export(선택)
        if opts['export']:
            print("[GUI] ── JSONL export ──")
            exp = _load_from_file('export_jsonl', os.path.join('pipeline', 'naver', 'export_jsonl.py'))
            if opts['mode'] == 'local':
                exp.LAB = opts['folder']
            elif getattr(sys, 'frozen', False):
                exp.LAB = os.path.join(app_dir(), 'lab')
            try:
                os.makedirs(exp.LAB, exist_ok=True)
            except Exception:
                pass
            sys.argv = ['export_jsonl']
            try:
                exp.main()
            except Exception as e:
                print(f"[GUI] export 스킵({e})")

        # 5) CSV 저장(로컬 모드) — 엑셀에서 바로 열림
        if opts['mode'] == 'local' and opts.get('csv'):
            try:
                out, n = _export_csv(os.environ['SAMSAM_SQLITE_PATH'], opts['folder'])
                print(f"[GUI] 📄 CSV 저장: {out}  ({n:,}건)")
            except Exception as e:
                print(f"[GUI] CSV 저장 실패: {e}")

        print("[GUI] ✅ 전체 완료")
    except Exception:
        print("[GUI] ❌ 오류:\n" + traceback.format_exc())
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        log_q.put(('__DONE__',))


def selftest():
    _add_paths()
    os.environ.setdefault('DATABASE_URL', 'postg://selftest')
    os.environ.setdefault('SAMSAM_SQLITE_PATH', os.path.join(app_dir(), '_selftest_naver.db'))
    lines, ok = [], True
    for name, rel in [('subway', 'common/subway.py'),
                      ('local_db', 'common/local_db.py'),
                      ('db', 'common/naver_local_db.py'),
                      ('crawler', 'pipeline/naver/crawler.py'),
                      ('detail_map', 'pipeline/naver/detail_map.py'),
                      ('crawl_detail', 'pipeline/naver/crawl_detail.py'),
                      ('export_jsonl', 'pipeline/naver/export_jsonl.py')]:
        try:
            _load_from_file(name, rel)
            lines.append(f"  OK  {rel}")
        except Exception as e:
            ok = False
            lines.append(f"  FAIL {rel}: {e}")
    # 로컬 SQLite 스키마 생성까지 확인
    try:
        sys.modules['db'].init_db()
        lines.append("  OK  naver_local_db.init_db()")
    except Exception as e:
        ok = False
        lines.append(f"  FAIL init_db: {e}")
    for mod in ('tkinter', 'sqlite3', 'playwright.sync_api'):
        try:
            __import__(mod)
            lines.append(f"  OK  import {mod}")
        except Exception as e:
            ok = False
            lines.append(f"  FAIL import {mod}: {e}")
    lines.append("SELFTEST " + ("PASS" if ok else "FAIL"))
    text = "\n".join(lines)
    print(text)
    try:
        with open(os.path.join(app_dir(), 'selftest_result.txt'), 'w', encoding='utf-8') as f:
            f.write(text + "\n")
    except Exception:
        pass
    return 0 if ok else 1


def launch_gui():
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox, filedialog

    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    root = tk.Tk()
    root.title('네이버 부동산 크롤러')
    root.geometry('780x680')

    log_q = queue.Queue()
    stop_flag = threading.Event()
    worker = {'thread': None}

    # ── 지역(전국 복수) ──
    rf = ttk.LabelFrame(root, text='지역 (전국 · 복수 선택)', padding=8)
    rf.pack(fill='x', padx=10, pady=(10, 4))
    saved_sidos = set(cfg.get('sidos', CAPITAL))
    sido_vars = {}
    PER_ROW = 6
    for i, s in enumerate(SIDOS):
        v = tk.BooleanVar(value=(s in saved_sidos))
        ttk.Checkbutton(rf, text=s, variable=v).grid(
            row=i // PER_ROW, column=i % PER_ROW, padx=6, pady=1, sticky='w')
        sido_vars[s] = v
    brow = ttk.Frame(rf)
    brow.grid(row=(len(SIDOS) // PER_ROW) + 1, column=0, columnspan=PER_ROW, sticky='w', pady=(4, 0))

    def _set_sidos(names):
        for s, v in sido_vars.items():
            v.set(s in names)

    ttk.Button(brow, text='전국 전체', width=9,
               command=lambda: _set_sidos(SIDOS)).pack(side='left')
    ttk.Button(brow, text='수도권만', width=9,
               command=lambda: _set_sidos(CAPITAL)).pack(side='left', padx=4)
    ttk.Button(brow, text='전체 해제', width=9,
               command=lambda: _set_sidos([])).pack(side='left')

    # ── 거래유형(복수) ──
    trf = ttk.LabelFrame(root, text='거래유형 (복수 선택)', padding=8)
    trf.pack(fill='x', padx=10, pady=4)
    saved_trades = set(cfg.get('trades', ['B2']))
    trade_vars = {}
    for i, (label, code) in enumerate(TRADE_TYPES):
        v = tk.BooleanVar(value=(code in saved_trades))
        ttk.Checkbutton(trf, text=label, variable=v).grid(row=0, column=i, padx=10)
        trade_vars[code] = v

    # ── 매물종류(복수) ──
    tf = ttk.LabelFrame(root, text='매물 종류 (복수 선택)', padding=8)
    tf.pack(fill='x', padx=10, pady=4)
    saved_types = set(cfg.get('types', [c for _, c in PROPERTY_TYPES]))
    type_vars = {}
    for i, (label, code) in enumerate(PROPERTY_TYPES):
        v = tk.BooleanVar(value=(code in saved_types))
        ttk.Checkbutton(tf, text=label, variable=v).grid(row=0, column=i, padx=6)
        type_vars[code] = v

    # ── 저장 위치 ──
    sf = ttk.LabelFrame(root, text='저장 위치', padding=8)
    sf.pack(fill='x', padx=10, pady=4)
    mode_var = tk.StringVar(value=cfg.get('mode', 'local'))
    mrow = ttk.Frame(sf)
    mrow.grid(row=0, column=0, columnspan=3, sticky='w')
    e_db = ttk.Entry(sf, width=64)
    e_db.insert(0, cfg.get('database_url', ''))
    e_folder = ttk.Entry(sf, width=52)
    e_folder.insert(0, cfg.get('folder', ''))
    lbl_db = ttk.Label(sf, text='DB URL', width=10)
    lbl_folder = ttk.Label(sf, text='저장 폴더', width=10)

    def browse():
        d = filedialog.askdirectory(title='데이터를 쌓을 폴더 선택')
        if d:
            e_folder.delete(0, 'end'); e_folder.insert(0, d)

    btn_browse = ttk.Button(sf, text='찾아보기', command=browse)

    def sync_mode():
        if mode_var.get() == 'local':
            lbl_db.grid_remove(); e_db.grid_remove()
            lbl_folder.grid(row=1, column=0, sticky='w', pady=2)
            e_folder.grid(row=1, column=1, sticky='we', pady=2)
            btn_browse.grid(row=1, column=2, padx=4)
        else:
            lbl_folder.grid_remove(); e_folder.grid_remove(); btn_browse.grid_remove()
            lbl_db.grid(row=1, column=0, sticky='w', pady=2)
            e_db.grid(row=1, column=1, columnspan=2, sticky='we', pady=2)

    ttk.Radiobutton(mrow, text='로컬 폴더 (이 PC에 naver.db 로 쌓기)', value='local',
                    variable=mode_var, command=sync_mode).pack(side='left')
    ttk.Radiobutton(mrow, text='Supabase (DB URL)', value='supabase',
                    variable=mode_var, command=sync_mode).pack(side='left', padx=12)
    sync_mode()

    # ── 수집량 ──
    qf = ttk.LabelFrame(root, text='수집량', padding=8)
    qf.pack(fill='x', padx=10, pady=4)
    limit_mode = tk.StringVar(value=cfg.get('limit_mode', 'all'))
    ttk.Radiobutton(qf, text='전체 (선택한 지역·종류 다)', value='all',
                    variable=limit_mode).pack(side='left')
    ttk.Radiobutton(qf, text='각 종류', value='sample', variable=limit_mode).pack(side='left', padx=(16, 2))
    test_count = ttk.Spinbox(qf, from_=1, to=1000000, width=8)
    test_count.set(str(cfg.get('test_count', 10)))
    test_count.pack(side='left')
    ttk.Label(qf, text='개씩만 (빠른 샘플)').pack(side='left', padx=(2, 0))

    # ── 옵션 ──
    of = ttk.Frame(root, padding=(10, 2))
    of.pack(fill='x')
    detail_var = tk.BooleanVar(value=cfg.get('detail', False))
    csv_var = tk.BooleanVar(value=cfg.get('csv', True))
    export_var = tk.BooleanVar(value=cfg.get('export', False))
    save_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(of, text='CSV로 저장 (엑셀)', variable=csv_var).pack(side='left')
    ttk.Checkbutton(of, text='상세정보도 수집 (느림)', variable=detail_var).pack(side='left', padx=10)
    ttk.Checkbutton(of, text='JSONL export', variable=export_var).pack(side='left')
    ttk.Checkbutton(of, text='설정 저장', variable=save_var).pack(side='left', padx=10)

    bf = ttk.Frame(root, padding=10)
    bf.pack(fill='x')
    btn_start = ttk.Button(bf, text='크롤 시작')
    btn_start.pack(side='left')
    btn_stop = ttk.Button(bf, text='중지', state='disabled')
    btn_stop.pack(side='left', padx=8)
    status = ttk.Label(bf, text='대기 중')
    status.pack(side='left', padx=12)

    log = scrolledtext.ScrolledText(root, height=16, font=('Consolas', 9))
    log.pack(fill='both', expand=True, padx=10, pady=(0, 10))

    def append(s):
        log.insert('end', s); log.see('end')

    def poll_queue():
        try:
            while True:
                item = log_q.get_nowait()
                if isinstance(item, tuple) and item and item[0] == '__DONE__':
                    btn_start.config(state='normal'); btn_stop.config(state='disabled')
                    status.config(text='완료' if not stop_flag.is_set() else '중지됨')
                    worker['thread'] = None
                else:
                    append(item)
        except queue.Empty:
            pass
        root.after(150, poll_queue)

    def start():
        sidos = [s for s, v in sido_vars.items() if v.get()]
        trades = [c for c, v in trade_vars.items() if v.get()]
        types = [c for c, v in type_vars.items() if v.get()]
        mode = mode_var.get()
        folder = e_folder.get().strip()
        db_url = e_db.get().strip()
        if not sidos:
            messagebox.showwarning('입력 필요', '지역을 하나 이상 선택하세요.'); return
        if not trades:
            messagebox.showwarning('입력 필요', '거래유형을 하나 이상 선택하세요.'); return
        if not types:
            messagebox.showwarning('입력 필요', '매물 종류를 하나 이상 선택하세요.'); return
        if mode == 'supabase' and not db_url:
            messagebox.showwarning('입력 필요', 'Supabase 모드는 DB URL이 필요합니다.'); return
        if mode == 'local':
            if not folder:
                messagebox.showwarning('입력 필요', '데이터를 쌓을 폴더를 선택하세요.'); return
            if not os.path.isdir(folder):
                messagebox.showwarning('폴더 없음', f'폴더가 존재하지 않습니다:\n{folder}'); return
        try:
            tcount = max(1, int(test_count.get()))
        except (ValueError, tk.TclError):
            tcount = 10
        lmode = limit_mode.get()
        if save_var.get():
            try:
                with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                    json.dump({'sidos': sidos, 'trades': trades, 'types': types, 'mode': mode,
                               'folder': folder, 'database_url': db_url, 'test_count': tcount,
                               'limit_mode': lmode, 'csv': csv_var.get(),
                               'detail': detail_var.get(), 'export': export_var.get()},
                              f, ensure_ascii=False, indent=2)
            except Exception as e:
                append(f"[설정 저장 실패: {e}]\n")

        opts = {'sidos': sidos, 'trades': trades, 'types': types, 'mode': mode,
                'folder': folder, 'database_url': db_url, 'test_count': tcount,
                'sample': (lmode == 'sample'), 'csv': csv_var.get(),
                'detail': detail_var.get(), 'export': export_var.get()}
        log.delete('1.0', 'end')
        stop_flag.clear()
        btn_start.config(state='disabled'); btn_stop.config(state='normal')
        status.config(text='크롤 중…')
        t = threading.Thread(target=run_pipeline, args=(opts, log_q, stop_flag), daemon=True)
        worker['thread'] = t
        t.start()

    def stop():
        stop_flag.set()
        mod = sys.modules.get('crawler')   # 크롤 잡 루프가 다음 잡에서 즉시 멈추게 플래그 세팅
        if mod is not None:
            mod.STOP = True
        status.config(text='중지 중… (곧 멈춤)')
        append('\n[⏹ 중지 — 즉시 멈춥니다]\n')

    btn_start.config(command=start)
    btn_stop.config(command=stop)

    def on_close():
        if worker['thread'] and worker['thread'].is_alive():
            if not messagebox.askyesno('종료', '크롤이 진행 중입니다. 정말 종료할까요?'):
                return
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', on_close)
    append('지역·거래유형·매물종류를 고르고 [크롤 시작]을 누르세요.\n'
           '네이버부동산은 로그인 없이 크롤합니다. 첫 실행 시 지역 트리 탐색에 몇 분 걸립니다.\n\n')
    root.after(150, poll_queue)
    root.mainloop()


def browsertest():
    """frozen exe에서 실제 크롬 실행까지 확인(브라우저 탐지/설치 + 헤드리스 launch)."""
    _add_paths()
    _ensure_chromium()
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        b = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        b.close(); pw.stop()
        r = f"BROWSERTEST PASS (path={os.environ.get('PLAYWRIGHT_BROWSERS_PATH')})"
    except Exception as e:
        r = "BROWSERTEST FAIL: " + repr(e)[:300]
    print(r)
    try:
        with open(os.path.join(app_dir(), 'browsertest_result.txt'), 'w', encoding='utf-8') as f:
            f.write(r + "\n")
    except Exception:
        pass
    return 0 if 'PASS' in r else 1


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    if '--browsertest' in sys.argv:
        sys.exit(browsertest())
    launch_gui()
