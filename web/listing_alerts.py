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
SCAN_EVERY_MIN = 15          # 크론이 1분마다 불러도 실제 스캔은 이 간격으로만
MAX_PER_ALERT = 40           # 한 번에 카톡으로 알릴 매물 수 상한(그 이상은 '외 N건')
LIST_IN_MSG = 5              # 메시지에 상세히 적는 매물 수
TYPE_NAMES = {"APT": "아파트", "OPST": "오피스텔", "VL": "빌라", "OR": "원룸",
              "DDDGG": "단독/다가구", "SG": "상가", "JWJT": "전원주택"}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ── 회원용 CRUD ────────────────────────────────────────────────────────────
def list_for(conn, member_id):
    rows = conn.execute(
        "SELECT id, name, query, enabled, last_run_at, last_sent_at, sent_count, created_at "
        "FROM listing_alerts WHERE member_id=%s ORDER BY id", (member_id,)).fetchall()
    return [dict(r) for r in rows]


def create(conn, member_id, name, query):
    conn.execute(
        "INSERT INTO listing_alerts(member_id, name, query, enabled, last_run_at, created_at) "
        "VALUES(%s,%s,%s,TRUE,%s,%s)", (member_id, name, query, _now(), _now()))
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
    cols = ("article_no, building_name, building_type_code, sigungu, dong, deposit, "
            "rent_monthly, maintenance_monthly, area_exclusive_m2, floor_current, "
            "lat, lng, first_seen")
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


def _line(x):
    """카톡 한 줄: '역삼동 오피스텔 · 보증 1000/월 85 · 8.2평 3층'"""
    bits = [" ".join(b for b in (x.get("dong"), TYPE_NAMES.get(x.get("building_type_code"), "")) if b)]
    dep, rent = x.get("deposit"), x.get("rent_monthly")
    if rent:
        bits.append(f"보증 {int(dep or 0):,}/월 {int(rent):,}")
    if x.get("pyeong"):
        bits.append(f"{x['pyeong']}평")
    if x.get("floor_current") is not None:
        bits.append(f"{x['floor_current']}층")
    return "· " + " · ".join(bits)


def _message(alert, items):
    head = f"🔔 [{alert.get('name') or '매물 알림'}] 새 매물 {len(items)}건"
    lines = [_line(x) for x in items[:LIST_IN_MSG]]
    if len(items) > LIST_IN_MSG:
        lines.append(f"…외 {len(items) - LIST_IN_MSG}건")
    return head + "\n\n" + "\n".join(lines)


def link_of(alert):
    """알림에서 열 링크 — 저장한 조건 그대로 부동산 뷰어를 연다."""
    q = [(k, v) for k, v in parse_qsl(alert.get("query") or "", keep_blank_values=True)
         if k not in ("page", "size")]
    return f"{SITE}/gangnam/" + (("?" + urlencode(q, doseq=True)) if q else "")


def run_one(conn, alert, notify=True):
    """알림 1건 스캔(+발송). (신규건수, 발송여부) 반환."""
    items = find_new(conn, alert.get("query") or "", alert.get("last_run_at"))
    sent = False
    if items and notify:
        sent = bool(kakao_notify.send_to_member(
            conn, alert["member_id"], _message(alert, items), link_of(alert), "매물 보기"))
    now = _now()
    if sent:
        conn.execute(
            "UPDATE listing_alerts SET last_run_at=%s, last_sent_at=%s, "
            "sent_count=COALESCE(sent_count,0)+%s WHERE id=%s",
            (now, now, len(items), alert["id"]))
    else:
        # 발송 실패(카톡 미연결 등)면 last_run_at 을 안 올려 다음 스캔에서 다시 시도.
        if not items:
            conn.execute("UPDATE listing_alerts SET last_run_at=%s WHERE id=%s",
                         (now, alert["id"]))
    conn.commit()
    return len(items), sent


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

    alerts = [dict(r) for r in conn.execute(
        "SELECT id, member_id, name, query, last_run_at FROM listing_alerts "
        "WHERE enabled IS TRUE").fetchall()]
    total_new, total_sent = 0, 0
    for al in alerts:
        try:
            n, sent = run_one(conn, al)
        except Exception as e:
            print(f"[alerts] #{al['id']} 스캔 실패: {repr(e)[:150]}", flush=True)
            continue
        total_new += n
        total_sent += 1 if sent else 0
    print(f"[alerts] {len(alerts)}건 스캔 · 새매물 {total_new} · 카톡 {total_sent}건", flush=True)
    return {"alerts": len(alerts), "new": total_new, "sent": total_sent}
