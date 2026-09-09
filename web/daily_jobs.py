# -*- coding: utf-8 -*-
"""웹 프로세스 안에서 도는 하루 1회 배치 — 매매 실거래 갱신 + 주소 캐시 warm-up.

서버에는 동작하는 크론이 없다(외부 cron-job.org·GH Actions 스케줄 모두 07-30 이후
정지). 두 배치는 순수 HTTP+DB라 웹 컨테이너에서 돌려도 무해하므로, 알림 스캐너와
같은 방식(데몬 스레드 + pg advisory lock + kv_cache 마커)으로 하루 1회 돌린다.

  1) 아파트 매매 실거래(최근 2개월) 재수집 + 동별 집계 → 카드/모달의 '소비력' 수치
  2) 좌표→주소 캐시 warm-up(예산 GEO_BUDGET) → 카드에 도로명·지번이 뜨게

로컬 PC의 야간 크롤과 무관하게 서버 혼자 돌 수 있어야 해서 여기에 둔다.
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "common"), os.path.join(_ROOT, "pipeline")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

RUN_HOUR = 4              # 새벽 4시 이후 첫 기회에 실행(하루 1회)
APT_MONTHS = 2            # 실거래는 신고 지연이 있어 최근 2개월만 다시 받으면 충분
GEO_BUDGET = 50000        # 좌표→주소 하루 예산(카카오 로컬 한도 10만 안에서)
_LOCK_ID = 823403
_STARTED = False


def _marker(conn, key, value=None):
    if value is None:
        row = conn.execute("SELECT data FROM kv_cache WHERE k=%s", (key,)).fetchone()
        try:
            return json.loads(row[0]) if row and row[0] else {}
        except (ValueError, TypeError):
            return {}
    conn.execute(
        "INSERT INTO kv_cache(k,data,updated_at) VALUES(%s,%s,%s) "
        "ON CONFLICT (k) DO UPDATE SET data=EXCLUDED.data, updated_at=EXCLUDED.updated_at",
        (key, json.dumps(value), time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    return value


def run_once(conn, force=False):
    """오늘 아직 안 돌았으면 배치 실행. 실행했으면 요약 dict, 아니면 {'skipped': ...}"""
    today = time.strftime("%Y-%m-%d")
    if not force:
        if _marker(conn, "daily_jobs_last").get("date") == today:
            return {"skipped": "이미 오늘 실행"}
        if int(time.strftime("%H")) < RUN_HOUR:
            return {"skipped": f"{RUN_HOUR}시 이후 실행"}
    _marker(conn, "daily_jobs_last", {"date": today, "at": time.time()})

    out = {}
    try:
        import fetch_apt_trades as apt
        out["apt_rows"] = apt.collect(conn, APT_MONTHS)
        out["apt_dongs"] = apt.aggregate(conn)
    except Exception as e:
        out["apt_error"] = repr(e)[:150]
        print(f"[daily] 실거래 갱신 실패: {out['apt_error']}", flush=True)
    try:
        import fetch_geo_addresses as geo
        todo = geo._todo(conn, GEO_BUDGET)
        filled = 0
        for i in range(0, len(todo), geo.CHUNK):
            import geocode
            filled += len(geocode.fill(conn, todo[i:i + geo.CHUNK], budget=geo.CHUNK))
        out["geo_filled"] = filled
    except Exception as e:
        out["geo_error"] = repr(e)[:150]
        print(f"[daily] 주소 캐시 실패: {out['geo_error']}", flush=True)
    print(f"[daily] 배치 완료: {out}", flush=True)
    return out


def start(interval_sec=1800):
    """하루 1회 배치를 지키는 감시 스레드(프로세스당 1개)."""
    global _STARTED
    if _STARTED:
        return
    _STARTED = True

    def loop():
        import db
        while True:
            time.sleep(interval_sec)
            conn, locked = None, False
            try:
                conn = db.connect()
                try:
                    locked = bool(conn.execute(
                        f"SELECT pg_try_advisory_lock({_LOCK_ID})").fetchone()[0])
                except Exception:
                    locked = True
                if locked:
                    run_once(conn)
            except Exception as e:
                print(f"[daily] 오류: {repr(e)[:150]}", flush=True)
            finally:
                if conn is not None:
                    if locked:
                        try:
                            conn.execute(f"SELECT pg_advisory_unlock({_LOCK_ID})")
                        except Exception:
                            pass
                    conn.close()

    import threading
    threading.Thread(target=loop, daemon=True, name="daily-jobs").start()
    print(f"[daily] 배치 감시 시작 (매일 {RUN_HOUR}시 이후 1회)", flush=True)
