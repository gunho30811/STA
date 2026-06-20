# -*- coding: utf-8 -*-
"""
네이버페이 부동산(new.land.naver.com) 오피스텔 '월세' 매물 크롤러.

직접 requests 접근은 TLS 핑거프린팅으로 차단되므로 Playwright(실제 크롬)로
new.land 를 띄워 Authorization 토큰을 확보하고, 브라우저 컨텍스트 내부에서
fetch() 로 비공개 API(/api/regions, /api/articles)를 호출한다.

  - 지역: 서울시(1100000000) / 경기도(4100000000) / 인천시(2800000000)
          를 시/군/구/동 단위까지 재귀 드릴다운.
  - 매물: realEstateType=OPST(오피스텔), tradeType=B2(월세).
  - 저장: SQLite (db.py 스키마). 동 단위로 진행상태를 기록해 재개 가능.

사용:
  python crawler.py                 # 서울/경기/인천 전체 (오래 걸림)
  python crawler.py --sido 서울시    # 특정 시/도만
  python crawler.py --sido 서울시 --gu 강남구   # 특정 구만
  python crawler.py --limit-dongs 5  # 동 N개만 (테스트용)
"""
import argparse
import base64
import json
import os
import sys
import time
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright
import db

ROOTS = [
    ("서울시", "1100000000"),
    ("경기도", "4100000000"),
    ("인천시", "2800000000"),
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

FETCH_JS = """
async ([url, auth]) => {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 20000);   // 20초 후 강제 중단
  try {
    const res = await fetch(url, {
      headers: { 'authorization': auth, 'accept': '*/*', 'accept-language': 'ko-KR' },
      credentials: 'include', signal: ctrl.signal
    });
    const txt = await res.text();
    return { status: res.status, body: txt };
  } catch (e) { return { status: -1, body: String(e) }; }
  finally { clearTimeout(t); }
}
"""


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_price(s):
    """'1억 5,000' / '5,000' / '400' -> 만원 정수."""
    if not s:
        return None
    s = str(s).replace(" ", "")
    total = 0
    if "억" in s:
        a, _, b = s.partition("억")
        if a:
            total += int(a.replace(",", "")) * 10000
        s = b
    s = s.replace(",", "")
    if s.isdigit():
        total += int(s)
    return total if total else (0 if s == "0" else total)


class NaverLand:
    def __init__(self, headless=True):
        self.headless = headless
        self._pw = sync_playwright().start()
        self.auth = None
        self.auth_exp = 0
        self._launch()

    def _launch(self):
        self.browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-dev-shm-usage", "--no-sandbox",
                  "--disable-gpu", "--disable-extensions"])
        self.ctx = self.browser.new_context(user_agent=UA, locale="ko-KR",
                                            viewport={"width": 1366, "height": 900})
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(30000)
        self.page.on("request", self._sniff)
        self._load()

    def restart(self):
        """행(hang)/메모리 누적 대비 브라우저 완전 재시작."""
        print(f"[{now()}] 브라우저 재시작...")
        try:
            self.browser.close()
        except Exception:
            pass
        self.auth = None
        self.auth_exp = 0
        self._launch()

    def _sniff(self, req):
        a = req.headers.get("authorization")
        if a and a.startswith("Bearer"):
            self.auth = a
            try:
                payload = a.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                self.auth_exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0)
            except Exception:
                pass

    def _load(self):
        self.page.goto("https://new.land.naver.com/offices",
                       wait_until="domcontentloaded", timeout=60000)
        for _ in range(20):
            if self.auth:
                break
            time.sleep(0.5)
        if not self.auth:
            raise RuntimeError("Authorization 토큰 캡처 실패")
        print(f"[{now()}] 토큰 확보 (exp={dt.datetime.fromtimestamp(self.auth_exp)})")

    def _ensure_token(self):
        if self.auth_exp - time.time() < 300:  # 5분 미만 남으면 갱신
            print(f"[{now()}] 토큰 갱신...")
            self._load()

    def api(self, url, retries=4):
        st = None
        for i in range(retries):
            try:
                self._ensure_token()
                r = self.page.evaluate(FETCH_JS, [url, self.auth])
                st, body = r["status"], r["body"]
                if st == 200 and body:
                    try:
                        return json.loads(body)
                    except Exception:
                        pass
                if st == 401:           # 토큰 만료 -> 강제 갱신
                    self.auth_exp = 0
            except Exception as e:      # 브라우저 행/크래시 -> 재시작
                print(f"[{now()}] evaluate 예외: {repr(e)[:80]} -> 재시작")
                try:
                    self.restart()
                except Exception:
                    time.sleep(5)
            time.sleep(1.5 * (i + 1))
        print(f"[{now()}] API 실패: {url[:90]} (status={st})")
        return None

    def regions(self, cortarNo):
        j = self.api(f"https://new.land.naver.com/api/regions/list?cortarNo={cortarNo}")
        return (j or {}).get("regionList", [])

    def articles_page(self, cortarNo, page):
        url = (
            "https://new.land.naver.com/api/articles"
            f"?cortarNo={cortarNo}&order=rank"
            "&realEstateType=OPST&tradeType=B2"
            "&tag=%3A%3A%3A%3A%3A%3A%3A%3A"
            "&rentPriceMin=0&rentPriceMax=900000000"
            "&priceMin=0&priceMax=900000000"
            "&areaMin=0&areaMax=900000000"
            f"&page={page}&articleState="
        )
        return self.api(url)

    def close(self):
        try:
            self.browser.close()
        finally:
            self._pw.stop()


# ----------------------------------------------------------------------------- region tree

def build_region_tree(nl, roots, only_sido=None, only_gu=None):
    """동(leaf) 목록을 [(cortarNo, sido, sigungu, dong, lat, lon)] 로 반환."""
    dongs = []

    def walk(cortarNo, path):
        children = nl.regions(cortarNo)
        time.sleep(0.3)
        for ch in children:
            name = ch["cortarName"]
            ctype = ch["cortarType"]
            cno = ch["cortarNo"]
            if ctype == "sec":  # 동
                sido = path[0]
                sigungu = " ".join(path[1:]) if len(path) > 1 else ""
                dongs.append((cno, sido, sigungu, name,
                              ch.get("centerLat"), ch.get("centerLon")))
            else:               # city / dvsn -> 재귀
                if only_gu and len(path) == 1 and only_gu not in name:
                    # 시/도 바로 아래(구/시) 레벨에서 only_gu 필터
                    continue
                walk(cno, path + [name])

    for sido_name, root_no in roots:
        if only_sido and only_sido not in sido_name:
            continue
        print(f"[{now()}] 지역 트리 탐색: {sido_name}")
        walk(root_no, [sido_name])
    return dongs


def save_regions(dongs):
    conn = db.connect()
    conn.executemany(
        "INSERT OR REPLACE INTO regions(cortarNo,sido,sigungu,dong,lat,lon) VALUES(?,?,?,?,?,?)",
        dongs)
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------------- listings

def crawl_dong(nl, cortarNo, sido, sigungu, dong, max_pages=60):
    conn = db.connect()
    state = conn.execute("SELECT status FROM crawl_state WHERE cortarNo=?",
                         (cortarNo,)).fetchone()
    if state and state["status"] == "done":
        conn.close()
        return 0  # 이미 완료

    total = 0
    page = 1
    while page <= max_pages:
        j = nl.articles_page(cortarNo, page)
        if not j:
            break
        arts = j.get("articleList", [])
        if not arts:
            break
        rows = []
        for a in arts:
            rows.append((
                a.get("articleNo"), sido, sigungu, dong, cortarNo,
                a.get("articleName"), a.get("buildingName"),
                a.get("realEstateTypeName"), a.get("tradeTypeName"),
                parse_price(a.get("dealOrWarrantPrc")),  # 보증금
                parse_price(a.get("rentPrc")),           # 월세
                a.get("area1"), a.get("area2"), a.get("areaName"),
                a.get("floorInfo"), a.get("direction"), a.get("articleConfirmYmd"),
                a.get("articleFeatureDesc"),
                ",".join(a.get("tagList") or []),
                a.get("latitude"), a.get("longitude"),
                a.get("realtorName"), a.get("cpName"),
                a.get("representativeImgUrl"),
                f"https://new.land.naver.com/offices?articleNo={a.get('articleNo')}",
                now(),
            ))
        conn.executemany("""
            INSERT OR REPLACE INTO listings(
                articleNo,sido,sigungu,dong,cortarNo,articleName,buildingName,
                realEstateType,tradeType,deposit,rent,area_m2,area_real_m2,areaName,
                floorInfo,direction,confirmYmd,featureDesc,tags,lat,lon,
                realtorName,cpName,imgUrl,articleUrl,crawled_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        conn.commit()
        total += len(rows)
        if not j.get("isMoreData"):
            break
        page += 1
        time.sleep(0.4)

    conn.execute(
        "INSERT OR REPLACE INTO crawl_state(cortarNo,status,n_articles,updated_at) VALUES(?,?,?,?)",
        (cortarNo, "done", total, now()))
    conn.commit()
    conn.close()
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sido", help="서울시 / 경기도 / 인천시 중 하나만")
    ap.add_argument("--gu", help="특정 구/시 이름 필터 (예: 강남구)")
    ap.add_argument("--limit-dongs", type=int, default=0, help="동 N개만 (테스트)")
    ap.add_argument("--show", action="store_true", help="브라우저 표시")
    args = ap.parse_args()

    db.init_db()

    # 이미 region 트리가 DB에 있으면 재탐색 생략(재개 속도 향상)
    conn = db.connect()
    have = conn.execute("SELECT COUNT(*) FROM regions").fetchone()[0]
    dongs_db = None
    if have:
        q = "SELECT cortarNo,sido,sigungu,dong,lat,lon FROM regions WHERE 1=1"
        p = []
        if args.sido:
            q += " AND sido LIKE ?"; p.append(f"%{args.sido}%")
        if args.gu:
            q += " AND sigungu LIKE ?"; p.append(f"%{args.gu}%")
        dongs_db = [tuple(r) for r in conn.execute(q + " ORDER BY sido,sigungu,dong", p)]
    conn.close()

    nl = NaverLand(headless=not args.show)
    try:
        if dongs_db:
            dongs = dongs_db
            print(f"[{now()}] DB에서 동 목록 로드: {len(dongs)}개 (트리 재탐색 생략)")
        else:
            dongs = build_region_tree(nl, ROOTS, args.sido, args.gu)
            save_regions(dongs)
        print(f"[{now()}] 대상 동 수: {len(dongs)}")
        if args.limit_dongs:
            dongs = dongs[:args.limit_dongs]

        grand = 0
        for i, (cno, sido, sigungu, dong, lat, lon) in enumerate(dongs, 1):
            try:
                n = crawl_dong(nl, cno, sido, sigungu, dong)
            except Exception as e:
                print(f"[{now()}] 동 처리 실패 {sido} {sigungu} {dong}: {repr(e)[:80]}")
                try:
                    nl.restart()
                except Exception:
                    pass
                continue
            grand += n
            print(f"[{now()}] ({i}/{len(dongs)}) {sido} {sigungu} {dong}: {n}건 (누적 {grand})")
            if i % 150 == 0:        # 메모리 누적 방지: 주기적 재시작
                nl.restart()
        print(f"[{now()}] 완료. 총 {grand}건 수집.")
    finally:
        nl.close()


if __name__ == "__main__":
    main()
