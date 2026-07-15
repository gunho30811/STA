# -*- coding: utf-8 -*-
"""
rendit 삼삼엠투 크롤러 — GUI (.exe 배포용)

버튼 하나로 로컬(한국 IP) PC에서 삼삼엠투 크롤 → Supabase 적재를 실행한다.
GitHub Actions(미국 데이터센터 IP)에서는 삼삼 스케줄 API가 빈값(소프트차단)이라
예약률 갱신이 안 되지만, 이 프로그램은 사용자 PC에서 돌아 그 문제가 없다.

동작:
  [크롤 시작] → crawler.py(신규+예약률 갱신) → snapshot.py(스냅샷) → export_jsonl.py(파일 export)
  진행 로그가 창에 실시간으로 흐르고, 끝나면 완료 표시.

접속정보(DB URL·삼삼 계정)는 코드에 박지 않고 창에서 직접 입력한다.
입력값은 같은 폴더의 rendit_crawler_config.json 에 저장돼 다음 실행 때 자동으로 채워진다.
(본인 PC 로컬 파일이며, 원치 않으면 '이 PC에 저장' 체크를 끄면 저장하지 않는다.)

개발 실행:  python gui/rendit_crawler.py
자체 점검:  python gui/rendit_crawler.py --selftest   (모듈/경로 임포트만 확인, 크롤 안 함)
"""
import importlib.util
import json
import os
import queue
import sys
import threading
import traceback

# ── 경로: 개발이면 레포 루트, 얼린(exe) 상태면 _MEIPASS ──────────────────────────
def resource_base():
    """번들 리소스(pipeline/, common/, db.py, data/)의 루트."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS  # PyInstaller 임시 추출 폴더
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir():
    """exe(또는 스크립트)가 있는 실제 폴더 — 설정·산출물 저장 위치."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE = resource_base()
CONFIG_PATH = os.path.join(app_dir(), 'rendit_crawler_config.json')

# 삼삼 매물종류: 화면 라벨 → API propertyTypes 코드 (2026-07 실측)
PROPERTY_TYPES = [
    ('오피스텔', 'OFFICETEL'),
    ('아파트', 'APARTMENT'),
    ('연립빌라', 'VILLA'),
    ('단독주택', 'DETACHED'),
    ('원룸건물', 'STUDIO'),
    ('상가주택', 'MIXED_USE'),
]


def _add_paths():
    """crawler/db/subway/export 임포트가 되도록 sys.path 구성."""
    for p in (BASE,
              os.path.join(BASE, 'common'),
              os.path.join(BASE, 'pipeline', 'samsam'),
              os.path.join(BASE, 'pipeline', 'naver')):
        if p not in sys.path:
            sys.path.insert(0, p)


def _load_from_file(name, relpath):
    """레포 트리의 .py 파일을 실제 경로에서 모듈로 로드(네이버/삼삼 crawler.py 이름충돌 회피)."""
    path = os.path.join(BASE, relpath)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── 로그를 GUI로 흘려보내는 stdout 대체 ─────────────────────────────────────────
class QueueWriter:
    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(s)

    def flush(self):
        pass

    # crawler.py 등이 import 시 sys.stdout.reconfigure(...)를 호출 → no-op 로 받아준다.
    def reconfigure(self, *a, **k):
        pass


def run_pipeline(fields, selected_codes, test_mode, mode, folder, log_q, stop_flag):
    """워커 스레드에서 실행: env 세팅 → crawler → snapshot → export. 로그는 log_q 로.

    mode='supabase' → DB URL 로 Supabase 적재. mode='local' → folder/samsam.db(SQLite) 누적.
    """
    # 1) 입력값을 환경변수로 (crawler/db 가 os.environ 에서 읽음. load_dotenv 는 기존 env 를 덮지 않음)
    os.environ['SAMSAM_EMAIL'] = fields['email1'].strip()
    os.environ['SAMSAM_PASSWORD'] = fields['password1']
    if fields['email2'].strip():
        os.environ['SAMSAM_EMAIL2'] = fields['email2'].strip()
        os.environ['SAMSAM_PASSWORD2'] = fields['password2']

    _add_paths()

    # 2) 저장 백엔드 선택 — 크롤러/스냅샷/export 의 `import db` 가 받을 모듈을 sys.modules 에 세팅.
    #    지난 실행이 주입해둔 걸 지우고 매번 새로 정한다.
    for m in ('db', 'crawler', 'snapshot', 'export_jsonl', 'subway'):
        sys.modules.pop(m, None)
    local_db = None
    if mode == 'local':
        os.environ['SAMSAM_SQLITE_PATH'] = os.path.join(folder, 'samsam.db')
        local_db = _load_from_file('db', os.path.join('common', 'local_db.py'))  # sys.modules['db'] 주입
    else:
        os.environ['DATABASE_URL'] = fields['database_url'].strip()

    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = QueueWriter(log_q)
    try:
        if mode == 'local':
            print(f"[GUI] 저장 위치: 로컬 폴더 → {os.environ['SAMSAM_SQLITE_PATH']}")
            local_db.init_db()  # 크롤러는 init_db 를 부르지 않으므로 테이블을 먼저 만든다
        else:
            print("[GUI] 저장 위치: Supabase(DB URL)")

        # subway 를 먼저 로드하고, 얼린 상태면 데이터 경로를 번들 위치로 교정
        subway = _load_from_file('subway', os.path.join('common', 'subway.py'))
        if getattr(sys, 'frozen', False):
            subway._DATA = os.path.join(BASE, 'data', 'subway_stations.csv')

        crawler = _load_from_file('crawler', os.path.join('pipeline', 'samsam', 'crawler.py'))
        crawler.PROPERTY_TYPES = list(selected_codes)  # 화면에서 고른 종류만

        print(f"[GUI] 크롤 시작 — 종류 {len(selected_codes)}개: {', '.join(selected_codes)}"
              + ("  (테스트: 각 50건)" if test_mode else ""))
        if stop_flag.is_set():
            return
        sys.argv = ['crawler'] + (['--limit', '50'] if test_mode else [])
        crawler.main()

        if stop_flag.is_set():
            print("[GUI] 중지 요청 — 스냅샷/ export 생략")
            return

        print("[GUI] ── 예약률 스냅샷 ──")
        snap = _load_from_file('snapshot', os.path.join('pipeline', 'samsam', 'snapshot.py'))
        sys.argv = ['snapshot']
        try:
            snap.main()
        except Exception as e:
            print(f"[GUI] 스냅샷 스킵({e})")

        print("[GUI] ── JSONL export ──")
        exp = _load_from_file('export_jsonl', os.path.join('pipeline', 'samsam', 'export_jsonl.py'))
        # 산출물(jsonl)을 어디에 둘지: 로컬 모드면 선택 폴더, 얼린(exe) 상태면 exe 옆 lab/
        if mode == 'local':
            exp.LAB = folder
        elif getattr(sys, 'frozen', False):
            exp.LAB = os.path.join(app_dir(), 'lab')
        os.makedirs(exp.LAB, exist_ok=True)
        sys.argv = ['export_jsonl']
        try:
            exp.main()
        except Exception as e:
            print(f"[GUI] export 스킵({e})")

        print("[GUI] ✅ 전체 완료")
    except Exception:
        print("[GUI] ❌ 오류:\n" + traceback.format_exc())
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        log_q.put(('__DONE__',))


# ── 자체 점검(크롤 안 하고 임포트/경로만 확인) ───────────────────────────────────
def selftest():
    _add_paths()
    os.environ.setdefault('DATABASE_URL', 'postg://selftest')  # db 임포트가 URL 없다고 죽지 않게
    lines, ok = [], True
    for name, rel in [('subway', 'common/subway.py'),
                      ('db', 'db.py'),
                      ('local_db', 'common/local_db.py'),
                      ('crawler', 'pipeline/samsam/crawler.py'),
                      ('snapshot', 'pipeline/samsam/snapshot.py'),
                      ('export_jsonl', 'pipeline/samsam/export_jsonl.py')]:
        try:
            _load_from_file(name, rel)
            lines.append(f"  OK  {rel}")
        except Exception as e:
            ok = False
            lines.append(f"  FAIL {rel}: {e}")
    for mod in ('tkinter', 'sqlite3', 'pg8000', 'requests', 'dotenv', 'playwright.sync_api'):
        try:
            __import__(mod)
            lines.append(f"  OK  import {mod}")
        except Exception as e:
            ok = False
            lines.append(f"  FAIL import {mod}: {e}")
    lines.append("SELFTEST " + ("PASS" if ok else "FAIL"))
    text = "\n".join(lines)
    print(text)
    # windowed(exe)에서는 stdout 이 안 보이므로 결과를 파일로도 남긴다.
    try:
        with open(os.path.join(app_dir(), 'selftest_result.txt'), 'w', encoding='utf-8') as f:
            f.write(text + "\n")
    except Exception:
        pass
    return 0 if ok else 1


# ── GUI ─────────────────────────────────────────────────────────────────────────
def launch_gui():
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox

    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    root = tk.Tk()
    root.title('rendit · 삼삼엠투 크롤러')
    root.geometry('760x600')

    log_q = queue.Queue()
    stop_flag = threading.Event()
    worker = {'thread': None}

    frm = ttk.Frame(root, padding=10)
    frm.pack(fill='x')

    def row(label, r, show=None, default=''):
        ttk.Label(frm, text=label, width=14).grid(row=r, column=0, sticky='w', pady=2)
        e = ttk.Entry(frm, width=70, show=show)
        e.grid(row=r, column=1, columnspan=3, sticky='we', pady=2)
        e.insert(0, default)
        return e

    e_em1 = row('삼삼 이메일 1', 1, default=cfg.get('email1', ''))
    e_pw1 = row('비밀번호 1', 2, show='*', default=cfg.get('password1', ''))
    e_em2 = row('삼삼 이메일 2', 3, default=cfg.get('email2', ''))
    e_pw2 = row('비밀번호 2', 4, show='*', default=cfg.get('password2', ''))
    ttk.Label(frm, text='(계정 2는 선택 — 있으면 하루 커버리지 2배)',
              foreground='#666').grid(row=5, column=1, sticky='w')

    # 저장 위치: Supabase(DB URL) 또는 로컬 폴더(SQLite 누적)
    mode_var = tk.StringVar(value=cfg.get('mode', 'local'))
    ttk.Label(frm, text='저장 위치', width=14).grid(row=6, column=0, sticky='w', pady=(8, 2))
    mrow = ttk.Frame(frm)
    mrow.grid(row=6, column=1, columnspan=3, sticky='w', pady=(8, 2))
    e_db = ttk.Entry(frm, width=70)
    e_db.insert(0, cfg.get('database_url', ''))
    e_folder = ttk.Entry(frm, width=58)
    e_folder.insert(0, cfg.get('folder', ''))

    def sync_mode():
        if mode_var.get() == 'local':
            e_db.grid_remove()
            lbl_db.grid_remove()
            lbl_folder.grid(); e_folder.grid(); btn_browse.grid()
        else:
            lbl_folder.grid_remove(); e_folder.grid_remove(); btn_browse.grid_remove()
            lbl_db.grid(); e_db.grid()

    ttk.Radiobutton(mrow, text='로컬 폴더 (이 PC에 쌓기)', value='local',
                    variable=mode_var, command=sync_mode).pack(side='left')
    ttk.Radiobutton(mrow, text='Supabase (DB URL)', value='supabase',
                    variable=mode_var, command=sync_mode).pack(side='left', padx=12)

    lbl_db = ttk.Label(frm, text='DB URL', width=14)
    lbl_db.grid(row=7, column=0, sticky='w', pady=2)
    e_db.grid(row=7, column=1, columnspan=3, sticky='we', pady=2)
    lbl_folder = ttk.Label(frm, text='저장 폴더', width=14)
    lbl_folder.grid(row=7, column=0, sticky='w', pady=2)
    e_folder.grid(row=7, column=1, columnspan=2, sticky='we', pady=2)

    def browse():
        from tkinter import filedialog
        d = filedialog.askdirectory(title='데이터를 쌓을 폴더 선택')
        if d:
            e_folder.delete(0, 'end'); e_folder.insert(0, d)

    btn_browse = ttk.Button(frm, text='찾아보기', command=browse)
    btn_browse.grid(row=7, column=3, sticky='e', pady=2)
    sync_mode()

    # 매물종류 체크박스
    tf = ttk.LabelFrame(root, text='가져올 매물 종류', padding=8)
    tf.pack(fill='x', padx=10, pady=(4, 4))
    saved_types = set(cfg.get('types', [c for _, c in PROPERTY_TYPES]))
    type_vars = {}
    for i, (label, code) in enumerate(PROPERTY_TYPES):
        v = tk.BooleanVar(value=(code in saved_types))
        ttk.Checkbutton(tf, text=label, variable=v).grid(row=0, column=i, padx=6)
        type_vars[code] = v

    of = ttk.Frame(root, padding=(10, 0))
    of.pack(fill='x')
    test_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(of, text='테스트 (각 종류 50건만)', variable=test_var).pack(side='left')
    save_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(of, text='이 PC에 입력값 저장', variable=save_var).pack(side='left', padx=12)

    bf = ttk.Frame(root, padding=10)
    bf.pack(fill='x')
    btn_start = ttk.Button(bf, text='크롤 시작')
    btn_start.pack(side='left')
    btn_stop = ttk.Button(bf, text='중지', state='disabled')
    btn_stop.pack(side='left', padx=8)
    status = ttk.Label(bf, text='대기 중')
    status.pack(side='left', padx=12)

    log = scrolledtext.ScrolledText(root, height=18, font=('Consolas', 9))
    log.pack(fill='both', expand=True, padx=10, pady=(0, 10))

    def append(s):
        log.insert('end', s)
        log.see('end')

    def poll_queue():
        try:
            while True:
                item = log_q.get_nowait()
                if isinstance(item, tuple) and item and item[0] == '__DONE__':
                    btn_start.config(state='normal')
                    btn_stop.config(state='disabled')
                    status.config(text='완료' if not stop_flag.is_set() else '중지됨')
                    worker['thread'] = None
                else:
                    append(item)
        except queue.Empty:
            pass
        root.after(150, poll_queue)

    def start():
        fields = {
            'database_url': e_db.get(), 'email1': e_em1.get(), 'password1': e_pw1.get(),
            'email2': e_em2.get(), 'password2': e_pw2.get(),
        }
        mode = mode_var.get()
        folder = e_folder.get().strip()
        if not fields['email1'] or not fields['password1']:
            messagebox.showwarning('입력 필요', '삼삼 이메일 1, 비밀번호 1은 필수입니다.')
            return
        if mode == 'supabase' and not fields['database_url'].strip():
            messagebox.showwarning('입력 필요', 'Supabase 모드는 DB URL이 필요합니다.')
            return
        if mode == 'local':
            if not folder:
                messagebox.showwarning('입력 필요', '데이터를 쌓을 폴더를 선택하세요.')
                return
            if not os.path.isdir(folder):
                messagebox.showwarning('폴더 없음', f'폴더가 존재하지 않습니다:\n{folder}')
                return
        codes = [c for c, v in type_vars.items() if v.get()]
        if not codes:
            messagebox.showwarning('입력 필요', '매물 종류를 하나 이상 선택하세요.')
            return
        if save_var.get():
            try:
                with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                    json.dump({**fields, 'types': codes, 'mode': mode, 'folder': folder},
                              f, ensure_ascii=False, indent=2)
            except Exception as e:
                append(f"[설정 저장 실패: {e}]\n")

        log.delete('1.0', 'end')
        stop_flag.clear()
        btn_start.config(state='disabled')
        btn_stop.config(state='normal')
        status.config(text='크롤 중…')
        t = threading.Thread(target=run_pipeline,
                             args=(fields, codes, test_var.get(), mode, folder, log_q, stop_flag),
                             daemon=True)
        worker['thread'] = t
        t.start()

    def stop():
        stop_flag.set()
        status.config(text='중지 요청 — 현재 배치까지 마치고 멈춥니다')
        append('\n[중지 요청됨 — 진행 중인 요청을 마치면 멈춥니다]\n')

    btn_start.config(command=start)
    btn_stop.config(command=stop)

    def on_close():
        if worker['thread'] and worker['thread'].is_alive():
            if not messagebox.askyesno('종료', '크롤이 진행 중입니다. 정말 종료할까요?'):
                return
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', on_close)
    append('접속정보와 매물 종류를 확인하고 [크롤 시작]을 누르세요.\n'
           'GitHub Actions와 달리 이 PC(한국 IP)에서 돌아 예약률까지 정상 수집됩니다.\n\n')
    root.after(150, poll_queue)
    root.mainloop()


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    launch_gui()
