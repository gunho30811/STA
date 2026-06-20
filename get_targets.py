# -*- coding: utf-8 -*-
import json, collections, requests, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 1) 네이버 전국 시/도 (모바일 getRegionList — requests로 동작)
UA="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
r=requests.get("https://m.land.naver.com/map/getRegionList?cortarNo=0000000000",
               headers={"User-Agent":UA,"Referer":"https://m.land.naver.com/"},timeout=20)
sido=r.json()["result"]["list"]
print("=== 네이버 전국 시/도 ===")
for s in sido:
    print(f"  {s['CortarNo']}  {s['CortarNm']}")

# 2) 삼삼 오피스텔 비수도권 시군구 집합
import os
DATA=os.path.join(os.path.dirname(os.path.abspath(__file__)),"data")
CACHE=os.path.join(DATA,"officetel_raw.jsonl")
SUDO={'서울특별시','경기도','인천광역시'}
by={}
for line in open(CACHE,encoding='utf-8'):
    line=line.strip()
    if line:
        try: o=json.loads(line); by[o['rid']]=o
        except: pass
non=[o for o in by.values() if o.get('state') not in SUDO]
print(f"\n비수도권 삼삼 오피스텔: {len(non)}개")
print("비수도권 시도 분포:")
for k,v in collections.Counter(o.get('state') for o in non).most_common():
    print(f"  {k}: {v}")
sgg=sorted(set((o.get('state'),o.get('province')) for o in non))
print(f"\n비수도권 시군구 수: {len(sgg)}")
print("시군구 이름 집합(naver 매칭용):")
names=sorted(set(o.get('province') for o in non if o.get('province')))
print(names)
# 저장
json.dump({'sido':[{'no':s['CortarNo'],'nm':s['CortarNm']} for s in sido],
           'target_sigungu':names,
           'sido_state_pairs':sorted(set(o.get('state') for o in non))},
          open(os.path.join(DATA,"targets.json"),"w",encoding='utf-8'),ensure_ascii=False,indent=2)
print("\n저장: data/targets.json")
