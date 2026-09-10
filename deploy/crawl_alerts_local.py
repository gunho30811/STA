# -*- coding: utf-8 -*-
"""조건 알림 전용 네이버 크롤 — 저장된 알림 조건의 동네만 짧은 주기로 다시 훑는다.

전체 크롤은 하루 한 번(02:00)이라 알림도 하루 한 번밖에 못 울린다. 회원이 알림에
정한 주기(1·2·4·6·12시간)마다 **그 알림이 보는 동네만** 크롤해서 새 매물을 빨리
잡아내는 것이 이 스크립트의 일. 네이버는 데이터센터 IP를 막으므로 서버가 아니라
가정용 IP인 이 PC에서 돈다(전체 크롤과 같은 이유).

  python deploy/crawl_alerts_local.py            # 주기가 된 알림만
  python deploy/crawl_alerts_local.py --all      # 켜진 알림 전부(주기 무시)
  python deploy/crawl_alerts_local.py --dry      # 크롤 계획만 출력

윈도우 작업 스케줄러(rendit-alerts-hourly)가 1시간마다 실행 → 주기가 된 알림만 처리.
크롤이 끝나면 서버의 알림 스캐너를 즉시 호출(/gangnam/api/cron-alerts)해 카톡을 보낸다.
"""
import argparse
import datetime
import os
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from socketserver import ThreadingTCPServer
from urllib.parse import parse_qsl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
from dotenv import load_dotenv  # noqa: E402

from crawl_samsam_local import _make_handler, _run, _ssh  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

LOCAL_PORT = 15434          # 삼삼(15432)·네이버 전체(15433)와 겹치지 않게
DEFAULT_TYPES = "APT,OPST,VL,OR,DDDGG,SG"
MAX_DONGS = 120             # 한 번에 도는 동 상한(알림이 전국급으로 넓으면 전체 크롤에 맡긴다)
RADIUS_MARGIN_M = 2500      # 역 반경 알림: 동 중심 좌표 기준이라 여유를 둔다


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _due_alerts(conn, force_all=False):
    """주기가 된(또는 전부) 켜진 알림."""
    sql = ("SELECT id, member_id, name, query, interval_hours, last_crawl_at "
           "FROM listing_alerts WHERE enabled IS TRUE")
    if not force_all:
        sql += (" AND (last_crawl_at IS NULL OR last_crawl_at <= to_char("
                "now() AT TIME ZONE 'Asia/Seoul' - make_interval(hours => "
                "COALESCE(interval_hours, 6)), 'YYYY-MM-DD HH24:MI:SS'))")
    return [dict(r) for r in conn.execute(sql + " ORDER BY id").fetchall()]


def _regions(conn):
    """크롤 대상 지역 트리 [(cortarno, sido, sigungu, dong, lat, lon)]."""
    return [tuple(r) for r in conn.execute(
        "SELECT cortarNo, sido, sigungu, dong, lat, lon FROM regions").fetchall()]


def _si_gu(sigungu):
    """'수원시 영통구' → ('수원시','영통구') · '강남구' → ('','강남구')  (웹의 _SI_SQL과 동일 규칙)"""
    if " " in (sigungu or ""):
        a, b = sigungu.split(" ", 1)
        return a, b
    if (sigungu or "").endswith("구"):
        return "", sigungu
    return sigungu or "", ""


def _match(region, spec):
    """region 행이 'sido|sigun|gu|dong' 스펙에 맞는지(빈 칸은 와일드카드)."""
    _, sido, sigungu, dong, _, _ = region
    si, gu = _si_gu(sigungu)
    want = (spec + ["", "", "", ""])[:4]
    for got, w in zip((sido, si, gu, dong), want):
        if w and got != w:
            return False
    return True


def plan_for(alert, regions):
    """알림 하나 → 크롤할 동 목록 [(cortarno, sido, dong)] 과 타입 코드.

    지역 조건이 전혀 없으면(전 지역 알림) 빈 목록 — 그런 알림은 일간 전체 크롤에 맡긴다."""
    q = parse_qsl(alert.get("query") or "", keep_blank_values=True)
    get_all = lambda k: [v for k2, v in q if k2 == k and v]
    get_one = lambda k: next((v for k2, v in q if k2 == k and v), "")

    specs = [r.split("|") for r in get_all("region")]
    casc = [get_one("sido"), get_one("sigun"), get_one("gu"), get_one("dong")]
    if any(casc):
        specs.append(casc)
    dongs_csv = [d for d in get_one("dongs").split(",") if d]
    if dongs_csv:
        specs += [["", "", "", d] for d in dongs_csv]

    hits = []
    if specs:
        hits = [r for r in regions if any(_match(r, s) for s in specs)]

    # 역/노선 반경 알림 — 동 중심이 반경 안(여유 포함)이면 대상
    stations = get_all("station")
    lines = get_all("line")
    if stations or lines:
        import subway
        coords = {}
        allc = {f"{n}역": (y, x) for n, y, x in subway._load()}
        allc.update(subway.line_station_coords())
        for s in stations:
            if s in allc:
                coords[s] = allc[s]
        for ln in lines:
            for s in subway.stations_of(ln):
                if s in allc:
                    coords[s] = allc[s]
        try:
            radius = float(get_one("radius") or 1000)
        except ValueError:
            radius = 1000.0
        radius += RADIUS_MARGIN_M
        near = [r for r in regions
                if r[4] is not None and r[5] is not None
                and any(subway.haversine_m(r[4], r[5], y, x) <= radius for y, x in coords.values())]
        hits = [r for r in hits if r in near] if specs else near

    # 제외 지역은 빼고
    xspecs = [r.split("|") for r in get_all("xregion")]
    if xspecs:
        hits = [r for r in hits if not any(_match(r, s) for s in xspecs)]

    types = get_one("types") or DEFAULT_TYPES
    return hits, types


def notify_server():
    """크롤 직후 서버 스캐너를 깨워 카톡을 바로 보낸다."""
    secret = os.environ.get("CRON_SECRET")
    domain = os.environ.get("RENDIT_DOMAIN", "rendits.duckdns.org")
    if not secret:
        print("[warn] CRON_SECRET 없음 — 서버 스캔 호출 생략(다음 주기 스캔에서 발송)", flush=True)
        return
    url = f"https://{domain}/gangnam/api/cron-alerts?key={urllib.parse.quote(secret)}&force=1"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            print(f"[알림] 서버 스캔 호출: {r.read().decode()[:200]}", flush=True)
    except Exception as e:
        print(f"[warn] 서버 스캔 호출 실패: {repr(e)[:120]}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="주기와 무관하게 켜진 알림 전부")
    ap.add_argument("--dry", action="store_true", help="크롤 계획만 출력")
    args = ap.parse_args()

    ssh = _ssh()
    pg_ip, _ = _run(ssh, "sudo docker inspect pg "
                    "--format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'")
    pw, _ = _run(ssh, "grep '^LOCAL_PG_PASSWORD=' /home/ubuntu/STA/.env | cut -d= -f2-")
    if not pg_ip or not pw:
        print(f"[FATAL] pg_ip={pg_ip!r} pw_len={len(pw)} — 조회 실패", flush=True)
        sys.exit(1)
    # 포트가 이미 쓰이면(다른 크롤이 도는 중) 다음 포트로 물러난다.
    port = LOCAL_PORT
    for _ in range(10):
        try:
            server = ThreadingTCPServer(("127.0.0.1", port),
                                        _make_handler(ssh.get_transport(), pg_ip, 5432))
            break
        except OSError:
            port += 1
    else:
        print("[FATAL] 로컬 터널 포트를 못 잡음", flush=True)
        sys.exit(1)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env["DATABASE_URL"] = ("postgresql://postgres:%s@127.0.0.1:%d/rendit?sslmode=disable"
                           % (urllib.parse.quote(pw, safe=""), port))
    env["PYTHONPATH"] = ROOT + os.pathsep + os.path.join(ROOT, "pipeline", "naver")
    os.environ["DATABASE_URL"] = env["DATABASE_URL"]

    import db
    conn = db.connect()
    try:
        alerts = _due_alerts(conn, args.all)
        if not alerts:
            print(f"[{now()}] 주기가 된 알림 없음 — 종료", flush=True)
            return
        regions = _regions(conn)
        # 여러 알림이 같은 동을 보면 한 번만 크롤한다: (시도 → 동이름) 로 묶고 타입은 합집합.
        by_sido, types = {}, set()
        cortars = set()
        for al in alerts:
            hits, tp = plan_for(al, regions)
            if not hits:
                print(f"  #{al['id']} {al['name']}: 지역 조건이 없어 건너뜀(일간 전체 크롤 담당)",
                      flush=True)
                continue
            if len(hits) > MAX_DONGS:
                print(f"  #{al['id']} {al['name']}: 대상 동 {len(hits)}개(>{MAX_DONGS}) — "
                      "너무 넓어 건너뜀(일간 전체 크롤 담당)", flush=True)
                continue
            print(f"  #{al['id']} {al['name']}: 동 {len(hits)}개 · 타입 {tp} "
                  f"(주기 {al.get('interval_hours') or 6}시간)", flush=True)
            for cno, sido, sigungu, dong, _, _ in hits:
                by_sido.setdefault(sido, set()).add(dong)
                cortars.add(cno)
            types.update(t for t in tp.split(",") if t)

        if not by_sido:
            print(f"[{now()}] 크롤할 동 없음 — 종료", flush=True)
            return
        type_arg = ",".join(sorted(types)) or DEFAULT_TYPES
        total_dongs = sum(len(v) for v in by_sido.values())
        print(f"[{now()}] 알림 크롤 시작: {len(by_sido)}개 시도 · 동 {total_dongs}개 · 타입 {type_arg}",
              flush=True)
        if args.dry:
            for sido, dongs in by_sido.items():
                print(f"   {sido}: {sorted(dongs)}")
            return

        # crawler.py 는 crawl_state 가 'done' 인 동을 건너뛴다(일간 전체 크롤이 남긴 표시).
        # 알림 크롤은 '다시 훑는 것'이 목적이므로 대상 동의 진행상태를 먼저 지운다.
        #   진행상태 키: cortarNo | cortarNo:타입 | cortarNo:타입:거래유형
        if cortars:
            conn.execute("DELETE FROM crawl_state WHERE cortarNo = ANY(%s) "
                         "OR split_part(cortarNo, ':', 1) = ANY(%s)",
                         [sorted(cortars), sorted(cortars)])
            conn.commit()

        py = sys.executable
        for sido, dongs in by_sido.items():
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt",
                                             delete=False) as f:
                f.write("\n".join(sorted(dongs)))
                path = f.name
            cmd = [py, "pipeline/naver/crawler.py", "--sidos", sido,
                   "--types", type_arg, "--dongs-file", path]
            print(f"\n[run] {sido} 동 {len(dongs)}개", flush=True)
            p = subprocess.run(cmd, cwd=ROOT, env=env)
            os.unlink(path)
            if p.returncode != 0:
                print(f"[warn] {sido} 크롤 rc={p.returncode}", flush=True)

        # 알림 크롤이 남긴 crawl_state 는 지운다 — 일간 전체 크롤의 적재 검증(기대치 합계)이
        # 부풀지 않게.
        if cortars:
            conn.execute("DELETE FROM crawl_state WHERE cortarNo = ANY(%s) "
                         "OR split_part(cortarNo, ':', 1) = ANY(%s)",
                         [sorted(cortars), sorted(cortars)])
            conn.commit()
        stamp = now()
        conn.execute("UPDATE listing_alerts SET last_crawl_at=%s WHERE id = ANY(%s)",
                     (stamp, [a["id"] for a in alerts]))
        conn.commit()
        print(f"[{now()}] 알림 크롤 완료 — 서버 스캔 호출", flush=True)
    finally:
        conn.close()
    notify_server()


if __name__ == "__main__":
    main()
