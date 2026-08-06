# -*- coding: utf-8 -*-
"""심평원 병원정보서비스 → data/poi.csv 의 hospital 행 갱신.

수도권(서울/경기/인천) 상급종합(clCd=01)·종합병원(clCd=11)을 API에서 받아 좌표와 함께 적재한다.
단기임대 수요 근거: 통원·간병 가족의 주단위 체류는 대형병원 인근에서 발생한다.

  python pipeline/fetch_poi_hospital.py           # poi.csv 의 hospital 행만 교체
  python pipeline/fetch_poi_hospital.py --dry     # 파일 안 쓰고 결과만 출력

필요: .env 의 DATA_GO_KR_KEY (data.go.kr Encoding 인증키).
주의: serviceKey 는 반드시 **Encoding 키를 그대로** URL 에 붙일 것. Decoding 키를 쓰거나
      requests 의 params= 로 넘기면 '/'·'+' 가 재인코딩돼 401 Unauthorized 가 난다.
"""
import argparse
import csv
import os
import sys
import time
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(os.path.join(BASE, ".env"))

OUT = os.path.join(BASE, "data", "poi.csv")
API = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
# 크롤 대상 지역과 맞춘다(2026-08-05 부산·충남 추가 — 충남은 천안 수요 근거용).
SIDO = {"110000": "서울", "310000": "경기", "230000": "인천",
        "210000": "부산", "340000": "충남"}
CLCD = {"01": "상급종합", "11": "종합병원"}


def fetch(sido_cd, cl_cd, key):
    """시도×종별 병원 목록 — [(name, lat, lon)]."""
    out, page = [], 1
    while True:
        url = (f"{API}?ServiceKey={key}&pageNo={page}&numOfRows=100"
               f"&sidoCd={sido_cd}&clCd={cl_cd}")
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        code = root.findtext(".//resultCode")
        if code not in ("00", "0000"):
            raise RuntimeError(f"API 오류 {code}: {root.findtext('.//resultMsg')}")
        items = root.findall(".//item")
        if not items:
            break
        for it in items:
            nm = (it.findtext("yadmNm") or "").strip()
            y, x = it.findtext("YPos"), it.findtext("XPos")   # 위도, 경도
            if not nm or not y or not x:
                continue
            try:
                out.append((nm, float(y), float(x)))
            except ValueError:
                continue
        total = int(root.findtext(".//totalCount") or 0)
        if page * 100 >= total:
            break
        page += 1
        time.sleep(0.3)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="파일에 쓰지 않고 결과만 출력")
    args = ap.parse_args()

    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        print("DATA_GO_KR_KEY 없음 — .env 확인"); return

    rows, seen = [], set()
    for sido_cd, sido_nm in SIDO.items():
        for cl_cd, cl_nm in CLCD.items():
            got = fetch(sido_cd, cl_cd, key)
            for nm, y, x in got:
                if nm in seen:      # 같은 이름 분원이 여러 시도에 걸칠 수 있어 이름 기준 dedup
                    continue
                seen.add(nm)
                rows.append(("hospital", nm, round(y, 6), round(x, 6)))
            print(f"  {sido_nm} {cl_nm}: {len(got)}건")
    print(f"병원 총 {len(rows)}곳 (중복 제거 후)")

    if args.dry:
        for r in rows[:5]:
            print("   ", r)
        return

    # 기존 poi.csv 에서 hospital 이 아닌 행(대학·산단·학원가·교통)은 그대로 두고 hospital 만 교체.
    keep = []
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            keep = [r for r in csv.DictReader(f) if r["kind"] != "hospital"]
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "name", "lat", "lon"])
        for kind, nm, y, x in sorted(rows, key=lambda r: r[1]):
            w.writerow([kind, nm, y, x])
        for r in keep:
            w.writerow([r["kind"], r["name"], r["lat"], r["lon"]])
    print(f"저장: {OUT} (hospital {len(rows)} + 기타 {len(keep)})")


if __name__ == "__main__":
    main()
