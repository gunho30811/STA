# -*- coding: utf-8 -*-
"""무거운 대시보드/추천 계산을 미리 돌려 DB(kv_cache)에 저장.

웹 요청 경로에서 20초+ 재계산(콜드스타트마다 반복)을 없애기 위함. 크롤 파이프라인 끝이나
크론(GitHub Actions)에서 주기적으로 호출한다. 웹은 kv_cache 를 즉시 읽기만 한다.

  python pipeline/refresh_insights.py

필요: .env DATABASE_URL. (POI/종사자/소비력 데이터는 data/ 정적 파일 + DB.)
"""
import json
import os
import sys
import datetime as dt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "web"))
sys.path.insert(0, os.path.join(BASE, "common"))

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE, ".env"))

import db


def _save(conn, key, data):
    conn.execute(
        "INSERT INTO kv_cache(k,data,updated_at) VALUES(%s,%s,%s)"
        " ON CONFLICT (k) DO UPDATE SET data=EXCLUDED.data,updated_at=EXCLUDED.updated_at",
        (key, json.dumps(data, ensure_ascii=False),
         dt.datetime.now().isoformat(timespec="seconds")))
    conn.commit()


def main():
    db.init_db()   # kv_cache 테이블 보장
    conn = db.connect()

    # 1) 대시보드 인사이트
    import portal
    t = dt.datetime.now()
    ins = portal._compute_insights(conn)
    _save(conn, "dashboard_insights", ins)
    print(f"dashboard_insights 저장: 스팟 {len(ins['spots'])}·미개척 {len(ins['unclaimed'])} "
          f"({(dt.datetime.now()-t).total_seconds():.1f}s)")

    # 2) 추천 후보(동 수요점수) — 매물 매칭은 파라미터별이라 웹에서, 후보만 미리.
    try:
        import samsam_app
        t = dt.datetime.now()
        cands = samsam_app._reco_candidates(conn)
        _save(conn, "reco_candidates_v2", cands)   # v2 = sido 포함 스키마
        print(f"reco_candidates_v2 저장: {len(cands)}곳 "
              f"({(dt.datetime.now()-t).total_seconds():.1f}s)")
    except Exception as e:
        print(f"reco_candidates 스킵: {repr(e)[:100]}")

    conn.close()
    print("완료")


if __name__ == "__main__":
    main()
