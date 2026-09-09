# -*- coding: utf-8 -*-
"""국토부 아파트 매매 실거래가를 받아 동네 시세(소비력 지표)를 만든다.

부동산 매물을 볼 때 "이 동네가 얼마나 사는 동네인지"를 가늠할 근거가 필요해서,
아파트 **매매 실거래가**(공공데이터포털 RTMSDataSvcAptTrade)를 시군구×월 단위로
받아 `apt_trades` 에 쌓고, 동별 중앙값·평당가를 `apt_price_dong` 으로 집계한다.
(우리가 크롤하는 네이버 매물은 월세 호가라 매매 시세는 여기서만 나온다.)

  python pipeline/fetch_apt_trades.py                 # 최근 12개월, 대상 전 시군구
  python pipeline/fetch_apt_trades.py --months 2      # 증분(최근 2개월)만 받아 갱신
  python pipeline/fetch_apt_trades.py --agg-only      # 수집 없이 집계만 다시
  python pipeline/fetch_apt_trades.py --sigungu 강남구  # 특정 시군구만

인증: .env 의 DATA_GO_KR_KEY (공공데이터포털 일반 인증키). 병원 POI 수집과 같은 키.
집계 창(WINDOW_MONTHS)은 12개월 — 동 단위 표본이 얇아 6개월로는 중앙값이 튄다.
동 표본이 MIN_N 미만이면 화면에서는 시군구 집계(dong='')로 폴백한다.
"""
import argparse
import os
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "common"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(os.path.join(BASE, ".env"))

import db  # noqa: E402
import target_regions  # noqa: E402

API = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
M2_PER_PYEONG = 3.305785
WINDOW_MONTHS = 12      # 집계 창
MIN_N = 5               # 동 집계 최소 거래건수(미만이면 시군구 집계로 폴백)
WORKERS = 1              # 이 API는 초당 요청제한이 빡빡해 동시호출 불가


def _months(n):
    """오늘부터 거슬러 n개월치 YYYYMM 리스트(최신 → 과거)."""
    y, m = date.today().year, date.today().month
    out = []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def _fetch(key, lawd_cd, ym, tries=6):
    """한 시군구·한 달치 거래 목록. 실패는 빈 리스트(로그만).

    이 API는 '초당 서비스 요청제한'이 빡빡해서(동시 호출을 늘리면 대부분 이 에러),
    한 번에 하나씩 부르고 제한에 걸리면 점점 더 기다렸다가 다시 시도한다."""
    rows, page, wait, errs = [], 1, 1.0, 0
    for _ in range(tries * 8):
        try:
            r = requests.get(API, params={"serviceKey": key, "LAWD_CD": lawd_cd,
                                          "DEAL_YMD": ym, "numOfRows": 1000,
                                          "pageNo": page}, timeout=30)
            root = ET.fromstring(r.text)
        except Exception as e:
            errs += 1                      # 일시적 연결 끊김은 몇 번 다시 시도
            if errs > 3:
                print(f"  [{lawd_cd} {ym}] 실패: {repr(e)[:90]}", flush=True)
                return rows
            time.sleep(2 * errs)
            continue
        code = root.findtext(".//resultCode")
        msg = (root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or "")
        if code not in ("000", "00"):
            if "요청제한" in msg or "LIMITED" in msg.upper():
                time.sleep(wait)
                wait = min(wait * 1.5, 3)      # 짧게 여러 번 — 길게 기다리면 전체가 늘어진다
                continue
            print(f"  [{lawd_cd} {ym}] API 오류 {code} {msg[:60]}", flush=True)
            return rows
        items = root.findall(".//item")
        rows += [{c.tag: (c.text or "").strip() for c in it} for it in items]
        total = int(root.findtext(".//totalCount") or 0)
        if len(rows) >= total or not items:
            return rows
        page += 1
    print(f"  [{lawd_cd} {ym}] 요청제한으로 포기", flush=True)
    return rows


def _row(d, lawd_cd, sido, sigungu):
    """API item → apt_trades 행. 금액·면적이 비면 None."""
    try:
        amount = int(d.get("dealAmount", "").replace(",", ""))
        area = float(d.get("excluUseAr") or 0)
    except ValueError:
        return None
    if not amount or area <= 0:
        return None
    ymd = (f"{d.get('dealYear')}-{int(d.get('dealMonth') or 0):02d}-"
           f"{int(d.get('dealDay') or 0):02d}")
    dong = d.get("umdNm") or ""
    apt = d.get("aptNm") or ""
    jibun = d.get("jibun") or ""
    floor = d.get("floor") or ""
    key = f"{lawd_cd}|{dong}|{apt}|{jibun}|{area}|{floor}|{ymd}|{amount}"
    return (key, lawd_cd, sido, sigungu, dong, apt, jibun, area,
            int(floor) if floor.lstrip("-").isdigit() else None,
            int(d["buildYear"]) if (d.get("buildYear") or "").isdigit() else None,
            amount, ymd, (d.get("cdealType") or "").strip() == "O",
            time.strftime("%Y-%m-%d %H:%M:%S"))


def targets(conn, only=None):
    """대상 시군구 [(lawd_cd, sido, sigungu)] — 크롤 대상 지역(common/target_regions)."""
    where, params = target_regions.sql_where()
    rows = conn.execute(
        f"SELECT DISTINCT substr(cortarno,1,5) cd, sido, sigungu FROM regions "
        f"WHERE {where} ORDER BY 1", params).fetchall()
    out = [(r[0], r[1], r[2]) for r in rows if r[0]]
    if only:
        out = [t for t in out if only in t[2] or only == t[0]]
    return out


def collect(conn, months, only=None):
    key = urllib.parse.unquote(os.environ.get("DATA_GO_KR_KEY", ""))
    if not key:
        raise SystemExit("DATA_GO_KR_KEY 없음 (.env)")
    tg = targets(conn, only)
    yms = _months(months)
    print(f"수집: 시군구 {len(tg)}개 × {len(yms)}개월 = {len(tg) * len(yms)}회 호출", flush=True)

    def one(job):
        lawd_cd, sido, sigungu, ym = job
        raw = _fetch(key, lawd_cd, ym)
        time.sleep(0.35)                      # 초당 요청제한 회피
        return [x for x in (_row(d, lawd_cd, sido, sigungu) for d in raw) if x]

    jobs = [(cd, sido, sgg, ym) for cd, sido, sgg in tg for ym in yms]
    total, done = 0, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        buf = []
        for rows in ex.map(one, jobs):
            buf += rows
            done += 1
            if len(buf) >= 2000:
                total += _save(conn, buf); buf = []
            if done % 100 == 0:
                print(f"  {done}/{len(jobs)}회 · 누적 {total + len(buf):,}건", flush=True)
        total += _save(conn, buf)
    print(f"수집 완료: {total:,}건 적재(중복 무시)", flush=True)
    return total


def _save(conn, rows):
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO apt_trades(trade_key, lawd_cd, sido, sigungu, dong, apt_name, jibun,"
        " area_m2, floor, build_year, amount, deal_date, canceled, collected_at)"
        " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (trade_key) DO NOTHING", rows)
    conn.commit()
    return len(rows)


def aggregate(conn):
    """동별·시군구별 매매 시세 집계 → apt_price_dong (dong='' 행이 시군구 전체)."""
    since = _months(WINDOW_MONTHS)[-1] + "-01"
    rows = conn.execute(
        "SELECT sido, sigungu, dong, amount, area_m2, apt_name, build_year FROM apt_trades "
        "WHERE canceled IS NOT TRUE AND deal_date >= %s", (since,)).fetchall()
    print(f"집계 대상 거래: {len(rows):,}건 (최근 {WINDOW_MONTHS}개월)", flush=True)

    buckets = {}
    for r in rows:
        pyeong_price = r["amount"] / (r["area_m2"] / M2_PER_PYEONG)
        for key in ((r["sigungu"], r["dong"]), (r["sigungu"], "")):
            b = buckets.setdefault(key, {"sido": r["sido"], "amt": [], "pp": [],
                                         "apt": {}, "yr": []})
            b["amt"].append(r["amount"])
            b["pp"].append(pyeong_price)
            if r["build_year"]:
                b["yr"].append(r["build_year"])
            a = b["apt"].setdefault(r["apt_name"], [])
            a.append(pyeong_price)

    def q(vals, p):
        v = sorted(vals)
        return v[min(len(v) - 1, int(len(v) * p))]

    out = []
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for (sigungu, dong), b in buckets.items():
        top = max(b["apt"].items(), key=lambda kv: (len(kv[1]), sum(kv[1]) / len(kv[1])))
        out.append((f"{sigungu}|{dong}", b["sido"], sigungu, dong, len(b["amt"]),
                    WINDOW_MONTHS, int(q(b["amt"], 0.5)), int(q(b["pp"], 0.5)),
                    int(q(b["pp"], 0.25)), int(q(b["pp"], 0.75)),
                    top[0], int(sum(top[1]) / len(top[1])),
                    int(sum(b["yr"]) / len(b["yr"])) if b["yr"] else None, now))
    conn.executemany(
        "INSERT INTO apt_price_dong(key, sido, sigungu, dong, n, months, median_amount,"
        " median_per_pyeong, p25_per_pyeong, p75_per_pyeong, top_apt, top_apt_per_pyeong,"
        " avg_build_year, updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (key) DO UPDATE SET n=EXCLUDED.n, months=EXCLUDED.months,"
        " median_amount=EXCLUDED.median_amount, median_per_pyeong=EXCLUDED.median_per_pyeong,"
        " p25_per_pyeong=EXCLUDED.p25_per_pyeong, p75_per_pyeong=EXCLUDED.p75_per_pyeong,"
        " top_apt=EXCLUDED.top_apt, top_apt_per_pyeong=EXCLUDED.top_apt_per_pyeong,"
        " avg_build_year=EXCLUDED.avg_build_year, updated_at=EXCLUDED.updated_at", out)
    conn.commit()
    dongs = sum(1 for k in buckets if k[1])
    print(f"집계 완료: 동 {dongs:,}개 + 시군구 {len(out) - dongs}개", flush=True)
    return len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=WINDOW_MONTHS, help="최근 N개월 수집")
    ap.add_argument("--sigungu", help="특정 시군구만(이름 부분일치 또는 법정동코드 5자리)")
    ap.add_argument("--agg-only", action="store_true", help="수집 없이 집계만")
    args = ap.parse_args()

    db.init_db()
    conn = db.connect()
    try:
        if not args.agg_only:
            collect(conn, args.months, args.sigungu)
        aggregate(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
