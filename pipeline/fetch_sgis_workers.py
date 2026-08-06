# -*- coding: utf-8 -*-
"""SGIS(통계청 통계지리정보) → data/dong_workers.csv 갱신 — 행정동별 사업체·종사자 수.

단기임대 수요의 가장 직접적 증거: "이 동네에 직장인이 몇 명 일하나".
종사자가 많은데 삼삼(단기임대) 매물이 없으면 = 출장·파견 수요 미개척 지역.

  python pipeline/fetch_sgis_workers.py          # 수도권 전 행정동 수집
  python pipeline/fetch_sgis_workers.py --dry    # 서울만 일부 출력

출력: data/dong_workers.csv (sido,sigungu,dong,adm_cd,corp_cnt,tot_worker,lat,lon)
필요: .env 의 DATA_SGIS_KR_ID / DATA_SGIS_KR_KEY (sgis.kostat.go.kr 개발자 키).
SGIS 좌표는 UTM-K(EPSG:5179) → 변환 API로 WGS84 전환.
"""
import argparse
import csv
import os
import sys
import time

import requests
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(os.path.join(BASE, ".env"))

OUT = os.path.join(BASE, "data", "dong_workers.csv")
SGIS = "https://sgisapi.kostat.go.kr/OpenAPI3"
# SGIS 시도코드(MOIS 법정동 코드와 다름 — SGIS 21=부산, 34=충남).
# 크롤 대상 지역과 맞춘다(2026-08-05 부산·충남 추가 — 충남은 천안 수요 근거용).
METRO = {"11": "서울특별시", "31": "경기도", "23": "인천광역시",
         "21": "부산광역시", "34": "충청남도"}
YEAR = "2022"   # 전국사업체조사 최신 제공연도


def auth():
    sid, sk = os.environ.get("DATA_SGIS_KR_ID"), os.environ.get("DATA_SGIS_KR_KEY")
    if not sid or not sk:
        raise RuntimeError("DATA_SGIS_KR_ID / DATA_SGIS_KR_KEY 없음 (.env)")
    r = requests.get(f"{SGIS}/auth/authentication.json",
                     params={"consumer_key": sid, "consumer_secret": sk}, timeout=25)
    return r.json()["result"]["accessToken"]


def transcoord(tok, x, y):
    """UTM-K(5179) → WGS84(4326) 단건 변환 → (lat, lon)."""
    r = requests.get(f"{SGIS}/transformation/transcoord.json",
                     params={"accessToken": tok, "src": "5179", "dst": "4326",
                             "posX": x, "posY": y}, timeout=25)
    d = r.json()["result"]
    return float(d["posY"]), float(d["posX"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    tok = auth()
    rows = []
    for sido_cd, sido_nm in METRO.items():
        # 시군구 목록
        gus = requests.get(f"{SGIS}/stats/company.json",
                           params={"accessToken": tok, "year": YEAR,
                                   "adm_cd": sido_cd, "low_search": "1"}, timeout=25).json()["result"]
        for gu in gus:
            gu_cd, gu_nm = gu["adm_cd"], gu["adm_nm"]
            # 구 하위 행정동
            try:
                dongs = requests.get(f"{SGIS}/stats/company.json",
                                     params={"accessToken": tok, "year": YEAR,
                                             "adm_cd": gu_cd, "low_search": "1"}, timeout=25).json()["result"]
            except Exception:
                continue
            # 동 좌표(구 단위로 한 번에)
            coords = {}
            try:
                for a in requests.get(f"{SGIS}/addr/stage.json",
                                      params={"accessToken": tok, "cd": gu_cd}, timeout=25).json()["result"]:
                    coords[a["addr_name"]] = (a["x_coor"], a["y_coor"])
            except Exception:
                pass
            for d in dongs:
                nm = d["adm_nm"]
                xy = coords.get(nm)
                lat = lon = None
                if xy:
                    try:
                        lat, lon = transcoord(tok, xy[0], xy[1])
                        time.sleep(0.05)
                    except Exception:
                        pass
                rows.append((sido_nm, gu_nm, nm, d["adm_cd"],
                             int(d.get("corp_cnt") or 0), int(d.get("tot_worker") or 0),
                             round(lat, 6) if lat else "", round(lon, 6) if lon else ""))
            print(f"  {sido_nm} {gu_nm}: {len(dongs)}개 동")
            if args.dry and sido_cd == "11":
                break
        if args.dry:
            break
    rows.sort(key=lambda r: -r[5])
    print(f"총 {len(rows)}개 행정동. 종사자 상위:")
    for r in rows[:10]:
        print(f"   {r[1]} {r[2]}: 종사자 {r[5]:,} / 사업체 {r[4]:,}")

    if args.dry:
        return
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sido", "sigungu", "dong", "adm_cd", "corp_cnt", "tot_worker", "lat", "lon"])
        w.writerows(rows)
    print(f"저장: {OUT} ({len(rows)}행)")


if __name__ == "__main__":
    main()
