# -*- coding: utf-8 -*-
"""
삼삼 매물 상세 페이지에서 도로명/지번 주소(층 포함)를 추가로 수집.
- web.33m2.co.kr/guest/room/{rid} 는 Next.js 서버렌더링이라 로그인 없이 일반 GET으로 충분
  (페이지 HTML에 "도로명"/"지번" 라벨과 값이 그대로 박혀 있음).
- officetel_raw.jsonl + oneroom_raw.jsonl 의 모든 rid 대상, 이어받기 지원.
- 저장: data/samsam_address.jsonl  {rid, road_addr, jibun_addr}
"""
import json, os, re, sys, time
import requests
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
OUT = os.path.join(DATA, "samsam_address.jsonl")

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
PAT = re.compile(r'<span class="font-semibold">([^<]+)</span><span>([^<]+)</span>')
SLEEP = 0.5
BLOCK_WAIT = 60


def log(m):
    print(m, flush=True)


def load_rids():
    rids = []
    for name in ("officetel_raw.jsonl", "oneroom_raw.jsonl"):
        path = os.path.join(DATA, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if 'rid' in o:
                    rids.append(str(o['rid']))
    return sorted(set(rids), key=int)


def load_done():
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                    done.add(str(o['rid']))
                except Exception:
                    continue
    return done


def fetch_addr(sess, rid):
    url = f'https://web.33m2.co.kr/guest/room/{rid}'
    r = sess.get(url, timeout=20)
    if r.status_code != 200:
        return r.status_code, None
    labels = dict(PAT.findall(r.text))
    return 200, {'road_addr': labels.get('도로명', ''), 'jibun_addr': labels.get('지번', '')}


def main():
    rids = load_rids()
    done = load_done()
    todo = [r for r in rids if r not in done]
    log(f"전체 {len(rids)}건, 이미 완료 {len(done)}건, 남음 {len(todo)}건")
    sess = requests.Session()
    sess.headers.update({'User-Agent': UA})
    with open(OUT, 'a', encoding='utf-8') as out:
        for i, rid in enumerate(todo, 1):
            for attempt in range(5):
                try:
                    status, addr = fetch_addr(sess, rid)
                except Exception as e:
                    log(f"[{rid}] 요청 실패({repr(e)[:60]}), 재시도")
                    time.sleep(3)
                    continue
                if status == 200:
                    rec = {'rid': rid, **(addr or {'road_addr': '', 'jibun_addr': ''})}
                    out.write(json.dumps(rec, ensure_ascii=False) + '\n')
                    out.flush()
                    break
                elif status in (403, 429):
                    log(f"[{rid}] {status} 차단 → {BLOCK_WAIT}s 대기")
                    time.sleep(BLOCK_WAIT)
                else:
                    log(f"[{rid}] status={status}, 빈 값으로 스킵")
                    rec = {'rid': rid, 'road_addr': '', 'jibun_addr': ''}
                    out.write(json.dumps(rec, ensure_ascii=False) + '\n')
                    out.flush()
                    break
            else:
                log(f"[{rid}] 5회 시도 실패, 빈 값으로 스킵")
                rec = {'rid': rid, 'road_addr': '', 'jibun_addr': ''}
                out.write(json.dumps(rec, ensure_ascii=False) + '\n')
                out.flush()
            time.sleep(SLEEP)
            if i % 200 == 0:
                log(f"진행 {i}/{len(todo)}")
    log("완료")


if __name__ == "__main__":
    main()
