# -*- coding: utf-8 -*-
"""부동산 매물 조건 알림 — 저장한 검색조건에 새 매물이 뜨면 카카오톡으로 보낸다.

회원이 부동산 뷰어에서 쓰던 필터를 그대로 저장해두면(listing_alerts.query = 목록 API
쿼리스트링), 주기적으로 '그 조건 + 지난 확인 이후 새로 들어온 매물'만 뽑아
카톡('나에게 보내기')으로 알린다.

신규 판정 기준은 listings.first_seen — 크롤러가 그 매물을 **처음 적재한 시각**
(db.py 마이그레이션의 컬럼 기본값). 네이버 확인일자(confirmed_at)는 중개사가 다시
확인만 해도 바뀌므로 '새 매물' 기준으로는 못 쓴다.

호출 지점
  - web/gangnam_app.py  /api/alerts*        (회원 CRUD·즉시 발송 테스트)
  - web/samsam_app.py   /chat/api/cron-poll (1분 크론에 얹어 15분마다 스캔)
"""
import json
import os
import sys
import time
from urllib.parse import parse_qsl, urlencode

from werkzeug.datastructures import MultiDict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))            # db
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "common"))   # kakao_notify 등
import kakao_notify  # noqa: E402

SITE = f"https://{os.environ.get('RENDIT_DOMAIN', 'rendits.duckdns.org')}"
SCAN_EVERY_MIN = 5           # 스캐너가 깨어나는 간격(실제 발송은 알림별 주기를 따른다)
INTERVAL_CHOICES = [1, 2, 4, 6, 12]      # 알림 주기(시간) — 화면에서 고를 수 있는 값
DEFAULT_INTERVAL = 6
MAX_PER_ALERT = 40           # 한 번에 카톡으로 알릴 매물 수 상한(그 이상은 '외 N건')
MSGS_PER_RUN = 5             # 한 번에 보낼 카톡 통수(매물 1건 = 1통, 넘으면 요약 1통 추가)
TYPE_NAMES = {"APT": "아파트", "OPST": "오피스텔", "VL": "빌라", "OR": "원룸",
              "DDDGG": "단독/다가구", "SG": "상가", "JWJT": "전원주택"}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ── 회원용 CRUD ────────────────────────────────────────────────────────────
def list_for(conn, member_id):
    rows = conn.execute(
        "SELECT id, name, query, enabled, last_run_at, last_sent_at, sent_count, created_at, "
        "interval_hours, last_crawl_at "
        "FROM listing_alerts WHERE member_id=%s ORDER BY id", (member_id,)).fetchall()
    return [dict(r) for r in rows]


def clean_interval(v):
    """화면에서 온 주기 값을 허용 목록 안으로."""
    try:
        h = int(v)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL
    return h if h in INTERVAL_CHOICES else DEFAULT_INTERVAL


def create(conn, member_id, name, query, interval_hours=DEFAULT_INTERVAL):
    conn.execute(
        "INSERT INTO listing_alerts(member_id, name, query, enabled, last_run_at, created_at,"
        " interval_hours) VALUES(%s,%s,%s,TRUE,%s,%s,%s)",
        (member_id, name, query, _now(), _now(), clean_interval(interval_hours)))
    conn.commit()


def set_interval(conn, member_id, alert_id, interval_hours):
    conn.execute("UPDATE listing_alerts SET interval_hours=%s WHERE id=%s AND member_id=%s",
                 (clean_interval(interval_hours), alert_id, member_id))
    conn.commit()


def delete(conn, member_id, alert_id):
    conn.execute("DELETE FROM listing_alerts WHERE id=%s AND member_id=%s",
                 (alert_id, member_id))
    conn.commit()


def set_enabled(conn, member_id, alert_id, enabled):
    conn.execute("UPDATE listing_alerts SET enabled=%s WHERE id=%s AND member_id=%s",
                 (bool(enabled), alert_id, member_id))
    conn.commit()


# ── 스캔 ──────────────────────────────────────────────────────────────────
def find_new(conn, query, since):
    """저장 조건(query 문자열) + first_seen > since 인 매물 목록.

    필터 해석은 부동산 뷰어와 **같은 코드**(gangnam_app._build_where)를 쓴다 —
    화면에서 본 결과와 알림 결과가 어긋나지 않게."""
    import gangnam_app as g

    a = MultiDict(parse_qsl(query, keep_blank_values=True))
    clauses, params = g._build_where(a)
    if since:
        clauses.append("first_seen > %s")
        params.append(since)
    else:
        clauses.append("first_seen IS NOT NULL")
    cols = ("article_no, building_name, building_type_code, sido, sigungu, dong, deposit, "
            "rent_monthly, maintenance_monthly, area_exclusive_m2, floor_current, floorinfo, rooms, "
            "direction, summary, subway_station, subway_distance_m, jibun_address, "
            "road_address, confirmed_at, lat, lng, first_seen")
    rows = conn.execute(
        f"SELECT {cols} FROM nl_live WHERE {' AND '.join(clauses)} "
        "ORDER BY first_seen DESC LIMIT 500", params).fetchall()
    items = [g._enrich_row(dict(r)) for r in rows]

    stns = g._stations_arg(a)
    if stns:
        try:
            radius = float(a.get("radius") or 1000)
        except ValueError:
            radius = 1000.0
        items = g._within_radius(items, [g.STATION_COORDS[s] for s in stns], radius)

    net_min = a.get("net_min")
    if net_min not in (None, ""):
        try:
            lo = float(net_min)
            for x in items:
                x["sam_area"] = g._area_of(x)
            items = [x for x in items
                     if (x.get("sam_area") or {}).get("net") is not None
                     and x["sam_area"]["net"] >= lo]
        except ValueError:
            pass
    return items[:MAX_PER_ALERT]


# ── 같은 매물 재발송 방지 ────────────────────────────────────────────────
# 네이버는 매물을 내렸다 다시 올리면 **새 articleNo**를 준다. 그래서 매물번호만으로는
# "어제 보낸 그 집"이 오늘 또 온다. 매물번호 + 물건 자체(동·건물·면적·층) 두 가지 키를
# 모두 기록해두고, 둘 중 하나라도 보낸 적 있으면 다시 보내지 않는다.
#   (가격은 키에 넣지 않는다 — 5만원 조정에 다시 울리면 그게 더 성가시다)
def dedup_keys(x):
    def _n(v, nd=0):
        return "" if v is None else (f"{float(v):.{nd}f}" if nd else str(int(float(v))))
    g = x.get("geo") or {}
    # 물건 키는 되도록 구체적으로 — 같은 건물의 다른 호실을 같은 물건으로 묶지 않게
    # 지번주소를 우선 쓰고(없으면 건물명), 면적·층·향까지 넣는다.
    where = (x.get("jibun_address") or g.get("jibun") or x.get("building_name") or "").strip()
    floor = _n(x.get("floor_current")) or (x.get("floorinfo") or "")
    unit = "|".join((x.get("sigungu") or "", x.get("dong") or "", where,
                     _n(x.get("area_exclusive_m2"), 1), floor,
                     (x.get("direction") or "")))
    return [f"a:{x.get('article_no')}", f"u:{unit}"]


def _drop_already_sent(conn, alert_id, items):
    """이 알림으로 이미 보낸 매물은 걸러낸다."""
    if not items:
        return items
    keys = [k for x in items for k in dedup_keys(x)]
    try:
        seen = {r[0] for r in conn.execute(
            "SELECT dedup_key FROM listing_alert_sent WHERE alert_id=%s AND dedup_key = ANY(%s)",
            (alert_id, keys)).fetchall()}
    except Exception as e:
        print(f"[alerts] 발송이력 조회 실패({repr(e)[:80]}) — 중복 필터 생략", flush=True)
        return items
    return [x for x in items if not any(k in seen for k in dedup_keys(x))]


def _mark_sent(conn, alert_id, items):
    rows = [(alert_id, k, x.get("article_no"), _now())
            for x in items for k in dedup_keys(x)]
    if not rows:
        return
    try:
        conn.executemany(
            "INSERT INTO listing_alert_sent(alert_id, dedup_key, article_no, sent_at) "
            "VALUES(%s,%s,%s,%s) ON CONFLICT (alert_id, dedup_key) DO NOTHING", rows)
        conn.commit()
    except Exception as e:
        print(f"[alerts] 발송이력 기록 실패: {repr(e)[:120]}", flush=True)


def _addr_of(conn, items):
    """주소 보강 — 상세 크롤이 없는 매물은 좌표→주소 캐시에서 가져온다(카톡에 주소를 싣기 위해)."""
    need = [x for x in items if not (x.get("jibun_address") or x.get("road_address"))]
    if not need:
        return
    try:
        import geocode
        geocode.attach(conn, need, ondemand=False)   # 알림 경로에선 API 호출 없이 캐시만
    except Exception:
        pass


def _detail_text(alert, x, idx, total):
    """매물 1건 = 카톡 1통. 웹을 안 열어도 판단이 되게 핵심을 다 적는다.

    카카오 텍스트 템플릿은 길이 제한이 빡빡해(200자 안팎) 한 통에 여러 건을 우겨넣으면
    잘린다 — 그래서 건별로 보낸다."""
    g = x.get("geo") or {}
    addr = (x.get("jibun_address") or g.get("jibun") or x.get("road_address") or g.get("road")
            or " ".join(b for b in (x.get("sido"), x.get("sigungu"), x.get("dong")) if b))
    name = (x.get("building_name") or "").strip()
    if not name or name in ("일반상가", "복합상가", "단지내상가", "일반원룸", "다가구",
                            "단독", "빌라", "상가", "원룸", "오피스텔", "아파트") or name.endswith("동"):
        name = g.get("building") or ""
    head = f"🔔 {alert.get('name') or '매물 알림'}"
    if total > 1:
        head += f" ({idx}/{total})"
    floor = (f"{x['floor_current']}층" if x.get("floor_current") is not None
             else (f"{x['floorinfo']}층" if x.get("floorinfo") else ""))
    line2 = " · ".join(b for b in (name, TYPE_NAMES.get(x.get("building_type_code"), ""), floor) if b)
    dep, rent = int(x.get("deposit") or 0), int(x.get("rent_monthly") or 0)
    price = f"💰 보증 {dep:,} / 월 {rent:,}"
    if x.get("maintenance_monthly"):
        price += f" (관리 {int(x['maintenance_monthly']):,})"
    spec = []
    if x.get("pyeong"):
        spec.append(f"{x['pyeong']}평")
    if x.get("area_exclusive_m2"):
        spec.append(f"전용 {round(float(x['area_exclusive_m2']))}㎡")
    if x.get("rooms") is not None:
        spec.append(f"방{x['rooms']}")
    if x.get("direction"):
        spec.append(str(x["direction"]).replace(" (거실 기준)", ""))
    sub = ""
    if x.get("subway_station"):
        sub = f"🚇 {x['subway_station']}"
        if x.get("subway_distance_m"):
            sub += f" {int(x['subway_distance_m'])}m"
    parts = [head, line2, price, " · ".join(spec) if spec else "", f"📍 {addr}", sub]
    if x.get("confirmed_at"):
        parts.append(f"확인일 {x['confirmed_at']}")
    smry = " ".join((x.get("summary") or "").split())
    if smry:
        parts.append(f"“{smry[:40]}”")
    return chr(10).join(p for p in parts if p)[:900]


def _summary_text(alert, items, shown):
    return (f"🔔 {alert.get('name') or '매물 알림'} — 새 매물 {len(items)}건 중 "
            f"{shown}건을 보냈습니다. 나머지 {len(items) - shown}건은 사이트에서 확인하세요.")


def item_link(x):
    """카톡 버튼이 여는 곳 — 우리 사이트의 그 매물 상세.

    네이버 매물번호 딥링크는 네이버가 경로를 없애 404가 뜬다(2026-09 확인). 우리 화면은
    주소·시세·주변 아파트 매매가·렌트 수익까지 같이 보여주므로 이쪽이 낫다."""
    return f"{SITE}/gangnam/?article={x.get('article_no')}"


def link_of(alert):
    """알림에서 열 링크 — 저장한 조건 그대로 부동산 뷰어를 연다."""
    q = [(k, v) for k, v in parse_qsl(alert.get("query") or "", keep_blank_values=True)
         if k not in ("page", "size")]
    return f"{SITE}/gangnam/" + (("?" + urlencode(q, doseq=True)) if q else "")


def run_one(conn, alert, notify=True):
    """알림 1건 스캔(+발송). (신규건수, 발송건수) 반환.

    같은 매물은 두 번 보내지 않는다(listing_alert_sent 기록) — 매물번호가 바뀌어
    다시 올라온 물건도 동·건물·면적·층 키로 걸러낸다."""
    items = find_new(conn, alert.get("query") or "", alert.get("last_run_at"))
    _addr_of(conn, items)          # 주소 보강 먼저 — 중복 판정 키에도 주소를 쓴다
    items = _drop_already_sent(conn, alert["id"], items)
    sent = 0
    if items and notify:
        show = items[:MSGS_PER_RUN]
        msgs = [(_detail_text(alert, x, i + 1, len(show)), item_link(x), "매물 상세 보기")
                for i, x in enumerate(show)]
        if len(items) > len(show):
            msgs.append((_summary_text(alert, items, len(show)), link_of(alert), "전체 보기"))
        sent = kakao_notify.send_many_to_member(conn, alert["member_id"], msgs)
        if sent:
            _mark_sent(conn, alert["id"], show)
    now = _now()
    if sent or not items:
        conn.execute(
            "UPDATE listing_alerts SET last_run_at=%s, last_sent_at=COALESCE(%s, last_sent_at), "
            "sent_count=COALESCE(sent_count,0)+%s WHERE id=%s",
            (now, now if sent else None, len(items) if sent else 0, alert["id"]))
    # 보낼 게 있는데 발송이 안 됐으면(카톡 미연결 등) last_run_at 을 안 올려 다음에 재시도.
    conn.commit()
    return len(items), sent


def start_scheduler(interval_sec=300):
    """웹 프로세스 안에서 주기 스캔 스레드를 띄운다(프로세스당 1개).

    외부 크론(cron-job.org)·GH Actions 스케줄이 죽어 있어도 알림이 도는 게 목적.
    gunicorn 워커가 여러 개면 스레드도 여러 개지만, pg advisory lock + kv_cache
    스로틀로 실제 스캔은 한 번만 돈다."""
    global _SCHED
    if _SCHED:
        return
    _SCHED = True

    def loop():
        import db
        while True:
            time.sleep(interval_sec)
            conn, locked = None, False
            try:
                conn = db.connect()
                try:
                    locked = bool(conn.execute("SELECT pg_try_advisory_lock(823402)").fetchone()[0])
                except Exception:
                    locked = True
                if locked:
                    run_due(conn)
            except Exception as e:
                print(f"[alerts] 주기 스캔 오류: {repr(e)[:150]}", flush=True)
            finally:
                if conn is not None:
                    if locked:
                        try:
                            conn.execute("SELECT pg_advisory_unlock(823402)")
                        except Exception:
                            pass
                    conn.close()

    import threading
    threading.Thread(target=loop, daemon=True, name="listing-alerts").start()
    print(f"[alerts] 주기 스캔 시작 ({interval_sec}s 간격, 실제 발송은 {SCAN_EVERY_MIN}분 간격)",
          flush=True)


_SCHED = False


def run_due(conn, force=False):
    """켜져 있는 전 회원 알림 스캔. 크론이 자주 불러도 SCAN_EVERY_MIN 간격으로만 실제 실행."""
    if not force:
        row = conn.execute("SELECT data FROM kv_cache WHERE k='listing_alerts_last'").fetchone()
        if row and row[0]:
            try:
                last = json.loads(row[0]).get("at", 0)
            except (ValueError, TypeError):
                last = 0
            if time.time() - last < SCAN_EVERY_MIN * 60:
                return {"skipped": True}
    conn.execute(
        "INSERT INTO kv_cache(k,data,updated_at) VALUES(%s,%s,%s) "
        "ON CONFLICT (k) DO UPDATE SET data=EXCLUDED.data, updated_at=EXCLUDED.updated_at",
        ("listing_alerts_last", json.dumps({"at": time.time()}), _now()))
    conn.commit()

    # 알림마다 주기(1·2·4·6·12시간)가 다르다 — 아직 주기가 안 된 알림은 건너뛴다.
    alerts = [dict(r) for r in conn.execute(
        "SELECT id, member_id, name, query, last_run_at, interval_hours FROM listing_alerts "
        "WHERE enabled IS TRUE AND (last_run_at IS NULL OR last_run_at <= to_char("
        "  now() AT TIME ZONE 'Asia/Seoul' - make_interval(hours => COALESCE(interval_hours, %s)),"
        "  'YYYY-MM-DD HH24:MI:SS'))", (DEFAULT_INTERVAL,)).fetchall()]
    total_new, total_sent = 0, 0
    for al in alerts:
        try:
            n, sent = run_one(conn, al)
        except Exception as e:
            print(f"[alerts] #{al['id']} 스캔 실패: {repr(e)[:150]}", flush=True)
            continue
        total_new += n
        total_sent += 1 if sent else 0
    if alerts:
        print(f"[alerts] {len(alerts)}건 스캔 · 새매물 {total_new} · 카톡 {total_sent}건", flush=True)
    return {"alerts": len(alerts), "new": total_new, "sent": total_sent}
