# -*- coding: utf-8 -*-
"""전용 풀스크린 지도(/map) 페이지 템플릿.

v6 — 바탕지도 카카오맵 전환 (Leaflet/OSM → Kakao Maps JS SDK):
- 같은 카카오 앱의 JavaScript 키 사용({{ kakao_js_key }} — portal이 env KAKAO_JS_KEY 주입).
- 줌 변환: 카카오 level ↔ 기존 줌 임계값은 z = 20 - level 로 매핑해 로직 그대로 재사용.
- 마커류는 전부 CustomOverlay(앵커 0,0 + CSS transform), 폴리곤은 kakao.maps.Polygon.
- 행정동 폴리곤 1,183개는 뷰포트 밖 setMap(null) 컬링(idle마다) — 팬/줌 성능 확보.
- v5까지의 기능 유지: 실제 행정동 경계 예약률 색칠·라벨, 동/시군구 건수 뱃지(폴리곤 꺼졌을 때),
  supercluster 개별 핀(z15+), POI 종류별 토글, ⭐추천 스팟, 검색, 필터, diff 렌더.
"""

MAP_PAGE = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>rendit · 지도</title>
<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard-dynamic-subset.css">
<style>
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;font-family:"Pretendard","Malgun Gothic",sans-serif;display:flex;flex-direction:column;background:#0f172a}
#wrap{flex:1;min-height:0;position:relative}
#map{position:absolute;inset:0}
.bar{position:absolute;z-index:1000;top:12px;left:54px;right:12px;display:flex;flex-direction:column;gap:6px;pointer-events:none}
.bar .row{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.bar .row>*{pointer-events:auto}
.tog,.chip{display:inline-flex;align-items:center;gap:5px;padding:6px 11px;border:1.5px solid #cbd5e1;border-radius:999px;
font-size:12px;font-weight:700;color:#94a3b8;background:rgba(255,255,255,.95);cursor:pointer;user-select:none;
box-shadow:0 1px 4px rgba(0,0,0,.2);white-space:nowrap}
.tog.on{color:#111827}
.chip{color:#475569}
.chip.on{background:#4321F3;border-color:#4321F3;color:#fff}
.dot{width:9px;height:9px;border-radius:50%;opacity:.35}.tog.on .dot{opacity:1}
.stat{font-size:11.5px;color:#e2e8f0;background:rgba(15,23,42,.75);padding:6px 11px;border-radius:8px}
.sbox{display:flex;align-items:center;gap:4px;background:rgba(255,255,255,.95);border:1.5px solid #cbd5e1;border-radius:999px;
padding:3px 6px 3px 12px;box-shadow:0 1px 4px rgba(0,0,0,.2)}
.sbox input{border:none;outline:none;background:none;font-size:13px;width:150px;font-weight:600}
.sbox button{border:none;background:#4321F3;color:#fff;border-radius:999px;padding:5px 11px;font-size:12px;font-weight:700;cursor:pointer}
.num{display:flex;align-items:center;gap:3px;background:rgba(255,255,255,.95);border:1.5px solid #cbd5e1;border-radius:999px;
padding:4px 10px;font-size:11.5px;font-weight:700;color:#475569;box-shadow:0 1px 4px rgba(0,0,0,.2)}
.num input{border:none;outline:none;background:none;width:44px;font-size:13px;font-weight:800;text-align:right;color:#111827}
.pin{width:16px;height:16px;border-radius:50%;border:2.5px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35)}
.poi{display:flex;align-items:center;gap:3px;background:rgba(255,255,255,.95);border:1.5px solid #f59e0b;border-radius:999px;
padding:2px 8px 2px 4px;font-size:11px;font-weight:800;color:#92400e;white-space:nowrap;box-shadow:0 1px 5px rgba(0,0,0,.3);
transform:translate(-50%,-50%)}
.poi.h{border-color:#dc2626;color:#991b1b}.poi.u{border-color:#2563eb;color:#1e40af}.poi.i{border-color:#7c3aed;color:#5b21b6}
.poi.a{border-color:#059669;color:#065f46}.poi.t{border-color:#0891b2;color:#155e75}
.poi.g{border-color:#db2777;color:#9d174d}
.poi.tsp{border-color:#f9a8d4;color:#be185d;font-size:10px;padding:1px 6px 1px 3px;opacity:.9}
.reco-badge{transform:translate(-50%,-50%);background:#fef9c3;border:2px solid #ca8a04;border-radius:12px;
padding:4px 10px;font-size:12px;font-weight:800;color:#854d0e;text-align:center;line-height:1.25;white-space:nowrap;
box-shadow:0 2px 8px rgba(0,0,0,.3);cursor:pointer}
.reco-badge b{font-size:15px;color:#a16207}.reco-n{font-weight:600;color:#a16207;font-size:10px}
.poi-lbl{font-size:11.5px;font-weight:800;color:#e2e8f0;background:rgba(15,23,42,.6);padding:5px 9px;border-radius:8px}
.ptog{display:inline-flex;align-items:center;padding:5px 10px;border:1.5px solid #cbd5e1;border-radius:999px;
font-size:12px;font-weight:700;color:#94a3b8;background:rgba(255,255,255,.9);cursor:pointer;user-select:none;
box-shadow:0 1px 4px rgba(0,0,0,.2);opacity:.55}
.ptog.on{color:#111827;opacity:1;border-color:#f59e0b}
.pin.nv{width:13px;height:13px;background:#14b8a6}
.clus{display:flex;align-items:center;justify-content:center;border-radius:50%;color:#fff;font-weight:800;
border:3px solid rgba(255,255,255,.85);box-shadow:0 2px 8px rgba(0,0,0,.35);line-height:1.1;text-align:center;cursor:pointer}
.clus.rent{background:#4321F3}
.clus small{font-weight:600;font-size:9px;display:block}
/* 네이버지도 스타일 지역(동/시군구) 건수 뱃지 — 예약률 표시와 안 겹치게 지점 위로 띄움 */
.dongb{transform:translate(-50%,-112%);display:flex;flex-direction:column;align-items:center;
background:rgba(67,33,243,.93);color:#fff;border:2.5px solid rgba(255,255,255,.9);border-radius:13px;
padding:4px 10px;line-height:1.15;white-space:nowrap;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.35);
font-weight:800;text-align:center}
.dongb b{font-size:14px}.dongb small{font-size:9px;font-weight:600;opacity:.85;margin-left:1px}
.dongb .dn{display:block;font-size:10px;font-weight:700;opacity:.92}
.dongb.mid b{font-size:15px}
.dongb.big{padding:5px 12px}.dongb.big b{font-size:16px}
/* 동 폴리곤 라벨: 흰 헤일로 텍스트(동명 + 예약률 + 렌트 건수) */
.dpl{transform:translate(-50%,-50%);text-align:center;font-weight:800;font-size:10.5px;line-height:1.25;
color:#334155;white-space:nowrap;pointer-events:none;
text-shadow:0 1px 3px #fff,0 -1px 3px #fff,1px 0 3px #fff,-1px 0 3px #fff,1px 1px 3px #fff,-1px -1px 3px #fff}
.dpl b{display:block;font-size:11.5px}
.dpl .pct{font-weight:900;font-size:11px}
.dpl .cnt{color:#94a3b8;font-size:9px;font-weight:600}
.dpl .rcnt{margin-left:4px;color:#4321F3;font-weight:900;font-size:10.5px}
.mback{position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:2000;display:flex;align-items:flex-end;justify-content:center}
.mcard{background:#fff;width:100%;max-width:560px;border-radius:16px 16px 0 0;max-height:78vh;display:flex;flex-direction:column;color:#1f2937}
@media(min-width:641px){.mback{align-items:center}.mcard{border-radius:16px}}
.mhead{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid #eef0f2}
.mtitle{font-size:15px;font-weight:800}
.mx{border:none;background:#f1f5f9;border-radius:8px;width:30px;height:30px;font-size:14px;cursor:pointer;color:#475569}
.mbody{overflow-y:auto;padding:4px 12px 14px}
.item{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:11px 8px;border-bottom:1px solid #f4f5f7;text-decoration:none;color:inherit;border-radius:8px}
.item:hover{background:#f8fafc}
.it-name{font-weight:700;font-size:13.5px}
.it-sub{font-size:11.5px;color:#64748b;margin-top:2px}
.it-occ{font-size:15px;font-weight:800;white-space:nowrap}
.it-go{font-size:11px;color:#4321F3;font-weight:700;white-space:nowrap}
.good{color:#059669}.midc{color:#d97706}.bad{color:#dc2626}.mut{color:#94a3b8}
@media(max-width:640px){.bar{left:12px;top:10px}.sbox input{width:100px}}
</style></head><body>
<div id=wrap>
<div id=map></div>
<div class=bar>
  <div class=row>
    <div class=sbox><input id=q placeholder="동 · 역 검색 (예: 역삼동, 강남역)"><button onclick="doSearch()">이동</button></div>
    <span class=num>주당≥<input id=f_week type=number placeholder=0>만</span>
    <span class=num>순수익≥<input id=f_net type=number placeholder=0>만</span>
    <span class=num>예약률≥<input id=f_occ type=number placeholder=0>%</span>
    <span class=num title="⭐ 추천 스팟 매물에 적용">보증금≤<input id=f_dep type=number placeholder=0>만</span>
    <span class=stat id=stat>렌트 매물 불러오는 중…</span>
  </div>
  <div class=row id=chips>
    <span class="chip on" data-t="">전체</span><span class=chip data-t="오피스텔">오피스텔</span>
    <span class=chip data-t="원룸건물">원룸</span><span class=chip data-t="아파트">아파트</span>
    <span class=chip data-t="연립빌라">연립빌라</span><span class=chip data-t="단독주택">단독주택</span>
    <span class=chip data-t="상가주택">상가주택</span>
  </div>
  <div class=row>
    <label class="tog on" id=t_circles><span class=dot style="background:#059669"></span>동별 예약률</label>
    <label class="tog" id=t_rent><span class=dot style="background:#4321F3"></span>렌트</label>
    <label class="tog" id=t_naver><span class=dot style="background:#14b8a6"></span>부동산</label>
    <label class="tog" id=t_reco><span class=dot style="background:#eab308"></span>⭐ 추천 스팟만</label>
  </div>
  <div class=row id=poirow>
    <span class=poi-lbl>수요시설</span>
    <label class="ptog" data-k=hospital>🏥 병원</label>
    <label class="ptog" data-k=university>🎓 대학</label>
    <label class="ptog" data-k=industrial>🏭 산단</label>
    <label class="ptog" data-k=academy>📚 학원</label>
    <label class="ptog" data-k=transport>🚄 교통</label>
    <label class="ptog" data-k=tour>🗼 관광</label>
  </div>
</div>
</div>
<div class=mback id=modal style="display:none" onclick="closeModal(event)">
  <div class=mcard onclick="event.stopPropagation()">
    <div class=mhead><div class=mtitle id=mtitle></div><button class=mx onclick="closeModal()">✕</button></div>
    <div class=mbody id=mbody></div>
  </div>
</div>
<script src="https://unpkg.com/supercluster@8.0.1/dist/supercluster.min.js"></script>
<script src="https://dapi.kakao.com/v2/maps/sdk.js?appkey={{ kakao_js_key }}&autoload=false"></script>
<script>
var NAVER_ZOOM=15, LIST_MAX=60
var NAV_TYPE={'오피스텔':'OPST','아파트':'APT','연립빌라':'VL','단독주택':'DDDGG','원룸건물':'OR','상가주택':'SG'}
function occColor(o){return o==null?'#94a3b8':o>=60?'#059669':o>=30?'#f59e0b':'#dc2626'}
function occCls(o){return o==null?'mut':o>=60?'good':o>=30?'midc':'bad'}
function median(arr){var a=arr.slice().sort(function(x,y){return x-y});var m=a.length>>1;return a.length%2?a[m]:(a[m-1]+a[m])/2}

var map=null
// 카카오 level ↔ 기존 줌 임계값 매핑(z = 20 - level) — 기존 티어 로직을 그대로 재사용.
function zv(){return 20-map.getLevel()}
function LL(lat,lng){return new kakao.maps.LatLng(lat,lng)}
function setViewZ(lat,lng,z){map.setLevel(Math.max(1,20-z));map.setCenter(LL(lat,lng))}
function viewBox(pad){
  var b=map.getBounds(), sw=b.getSouthWest(), ne=b.getNorthEast()
  var dy=(ne.getLat()-sw.getLat())*pad, dx=(ne.getLng()-sw.getLng())*pad
  var s=sw.getLat()-dy, w=sw.getLng()-dx, n=ne.getLat()+dy, e=ne.getLng()+dx
  return {s:s,w:w,n:n,e:e,contains:function(a){return a[0]>=s&&a[0]<=n&&a[1]>=w&&a[1]<=e}}
}
// divIcon 대체: CustomOverlay(앵커 0,0) + 콘텐츠 CSS transform. 클릭은 DOM 리스너로.
function mkOv(lat,lng,html,onClick,zidx){
  var el=document.createElement('div')
  el.innerHTML=html
  if(onClick){el.style.cursor='pointer';el.addEventListener('click',onClick)}
  return new kakao.maps.CustomOverlay({position:LL(lat,lng),content:el,xAnchor:0,yAnchor:0,
    clickable:!!onClick,zIndex:zidx||1})
}

var show={circles:true,rent:false,naver:false}
var poiShow={hospital:false,university:false,industrial:false,academy:false,transport:false,tour:false}
var FEATS=[], STATIONS=[], POIS=[], idx=null, DONG_AGG=[], SIG_AGG=[], rentShown=0
var GEO=null, GEO_STAT=[], GEO_IDX={}, GEOPOLYS=null   // 실제 행정동 경계 폴리곤 + 좌표귀속 통계
var filt={btype:'',week:0,occ:0,net:0,dep:0}
var shownRent=new Map(), shownDlbl=new Map(), shownPoi=new Map(), shownNav=[]
var recoOn=false

// ── 필터 적용: 인덱스 재구축(22k ~100ms) + 지역 집계 재계산 ──
function applyFilter(){
  var fs=FEATS.filter(function(f){
    var p=f.properties
    if(filt.btype && p.btype!==filt.btype) return false
    if(filt.week && !(p.week>=filt.week)) return false
    if(filt.occ && !(p.occ>=filt.occ)) return false
    if(filt.net && !(p.net!=null && p.net>=filt.net)) return false
    return true
  })
  rentShown=fs.length
  idx=new Supercluster({radius:56,maxZoom:17,minPoints:2}).load(fs)
  // 네이버지도식 지역 집계: 줌아웃에선 임의 클러스터 대신 실제 동/시군구 단위 건수 뱃지.
  // "당산동5가"·"한강로2가" 같은 법정동 조각은 기본 동으로 합쳐 뱃지 수를 줄임(네이버와 동일).
  var gd={}, gs={}
  fs.forEach(function(f){
    var p=f.properties
    var bd=(p.dong||'').replace(/\\d+가$/,'')
    var dk=(p.sigungu||'')+'|'+bd
    var d=gd[dk]||(gd[dk]={lats:[],lngs:[],n:0,label:bd||p.sigungu||'기타',items:[]})
    d.lats.push(p.lat); d.lngs.push(p.lng); d.n++; d.items.push(p)
    var sk=p.sigungu||'기타'
    var s=gs[sk]||(gs[sk]={lats:[],lngs:[],n:0,label:sk})
    s.lats.push(p.lat); s.lngs.push(p.lng); s.n++
  })
  DONG_AGG=Object.keys(gd).map(function(k){var v=gd[k];return [median(v.lats),median(v.lngs),v.n,v.label,k,v.items]})
  SIG_AGG=Object.keys(gs).map(function(k){var v=gs[k];return [median(v.lats),median(v.lngs),v.n,v.label,k,null]})
  // 동 폴리곤 통계(예약률·건수). 예약률은 매물 필터와 무관하게 항상 '전체' 기준 — 예약률 50%↑
  // 필터를 걸면 남은 매물 평균이라 모든 동이 높아 보이는 선택 편향 방지. 유형 칩만 세그먼트 반영.
  if(GEO){
    GEO_STAT=GEO.features.map(function(){return {occ:0,on:0,n:0,items:null}})
    var pop = filt.btype ? FEATS.filter(function(f){return f.properties.btype===filt.btype}) : FEATS
    pop.forEach(function(f){
      var p=f.properties
      if(p.di>=0){var s=GEO_STAT[p.di]; s.occ+=(p.occ||0); s.on++}
    })
    fs.forEach(function(f){
      var p=f.properties
      if(p.di>=0){var s=GEO_STAT[p.di]; s.n++; (s.items=s.items||[]).push(p)}
    })
    restyleGeo()
  }
  shownRent.forEach(function(o){o.setMap(null)}); shownRent.clear()
  shownDlbl.forEach(function(o){o.setMap(null)}); shownDlbl.clear()
  renderRent(); renderDongLabels(); loadNaver(); setStat()
}

// ── 모달 ──
function openList(items,cls){
  var t=document.getElementById('mtitle'), b=document.getElementById('mbody'), h=''
  if(cls==='rent'){
    items.sort(function(a,z){return (z.occ||0)-(a.occ||0)})
    t.textContent='렌트 매물 '+items.length+'개 (예약률순)'
    items.forEach(function(r){
      h+='<a class=item href="https://web.33m2.co.kr/guest/room/'+r.id+'" target=_blank rel=noreferrer>'+
        '<div><div class=it-name>'+(r.name||'(이름없음)')+'</div>'+
        '<div class=it-sub>'+(r.btype||'')+' · '+(r.pyeong!=null?r.pyeong+'평':'-')+' · 주당 '+(r.week!=null?r.week+'만':'-')+
        (r.net!=null?' · <b style=\\"color:'+(r.net>=0?'#059669':'#dc2626')+'\\">월순수익 '+r.net+'만</b>':'')+
        ' · '+(r.sigungu||'')+' '+(r.dong||'')+'</div></div>'+
        '<div style="text-align:right"><div class="it-occ '+occCls(r.occ)+'">'+(r.occ!=null?r.occ+'%':'-')+'</div>'+
        '<div class=it-go>매물 보기 →</div></div></a>'
    })
  }else{
    t.textContent='부동산 매물 '+items.length+'개'
    items.forEach(function(n){
      h+='<a class=item href="'+n.url+'" target=_blank rel=noreferrer>'+
        '<div><div class=it-name>'+(n.name||'(이름없음)')+'</div>'+
        '<div class=it-sub>보증금 '+(n.dep!=null?n.dep.toLocaleString():'-')+' / 월세 '+(n.rent!=null?n.rent:'-')+'만'+
        (n.m2?' · '+n.m2+'㎡':'')+(n.floor!=null?' · '+n.floor+'층':'')+'</div></div>'+
        '<div class=it-go>매물 보기 →</div></a>'
    })
  }
  b.innerHTML=h
  document.getElementById('modal').style.display='flex'
}
function closeModal(e){document.getElementById('modal').style.display='none'}

// ── diff 렌더(깜빡임 방지): key→CustomOverlay 재사용 ──
function diffRender(shownMap, wanted){
  shownMap.forEach(function(o,key){ if(!wanted.has(key)){o.setMap(null);shownMap.delete(key)} })
  wanted.forEach(function(make,key){
    if(shownMap.has(key)) return
    var o=make(); shownMap.set(key,o); o.setMap(map)
  })
}

function renderRent(){
  if(!idx) return
  var wanted=new Map()
  var z=zv()
  // 줌아웃(15 미만): 네이버지도처럼 실제 행정구역 단위 건수 — 14는 동(화면≈한 구), 13 이하는 시군구.
  // 동 폴리곤이 켜져 있으면 z14 건수는 폴리곤 라벨이 담당 → 동 뱃지는 폴리곤 꺼졌을 때만.
  if(show.rent && !recoOn && z<NAVER_ZOOM && !(z>=14 && show.circles && GEO)){
    var b2=viewBox(0.1), dong=z>=14
    var src=dong?DONG_AGG:SIG_AGG
    src.forEach(function(a){
      if(!b2.contains([a[0],a[1]])) return
      wanted.set((dong?'d':'s')+a[4], function(){
        var n=a[2]
        return mkOv(a[0],a[1],
          '<div class="dongb'+(n>=100?' big':n>=30?' mid':'')+'"><div><b>'+n+'</b><small>개</small></div><span class=dn>'+a[3]+'</span></div>',
          function(){
            if(dong&&n<=LIST_MAX) openList(a[5].slice(),'rent')
            else setViewZ(a[0],a[1],dong?15:14)
          },30)
      })
    })
  }
  // 확대(15+): 개별 매물 핀 — supercluster는 같은 지점에 겹친 매물 정리용으로만.
  if(show.rent && !recoOn && z>=NAVER_ZOOM){
    var b=viewBox(0.02)
    var items=idx.getClusters([b.w,b.s,b.e,b.n], z)
    items.forEach(function(f){
      var lng=f.geometry.coordinates[0], lat=f.geometry.coordinates[1]
      if(f.properties.cluster){
        var cid=f.properties.cluster_id, n=f.properties.point_count
        wanted.set('c'+cid+'_'+z, function(){
          var d=n>=1000?58:n>=100?52:n>=30?44:n>=10?38:32
          var txt=n>=1000?(Math.round(n/100)/10)+'k':n
          return mkOv(lat,lng,
            '<div style="transform:translate(-50%,-50%)"><div class="clus rent" style="width:'+d+'px;height:'+d+'px;font-size:13px">'+txt+'<small>개</small></div></div>',
            function(){
              if(n>LIST_MAX){setViewZ(lat,lng,Math.min(idx.getClusterExpansionZoom(cid),18))}
              else{openList(idx.getLeaves(cid,Infinity).map(function(l){return l.properties}),'rent')}
            },20)
        })
      }else{
        var r=f.properties
        wanted.set('p'+r.id, function(){
          return mkOv(lat,lng,
            '<div style="transform:translate(-50%,-50%)"><div class=pin style="background:'+occColor(r.occ)+'"></div></div>',
            function(){openList([r],'rent')},10)
        })
      }
    })
  }
  diffRender(shownRent, wanted)
}

// ── 실제 행정동 경계 폴리곤: 동 면적 그대로 예약률 색칠, 매물은 좌표→폴리곤 귀속 ──
// (매물 dong은 법정동, 경계는 행정동이라 이름 매칭이 안 됨 — 좌표 점-내부-판정으로 회피)
function eachOuter(f,cb){
  var g=f.geometry
  if(g.type==='Polygon') cb(g.coordinates[0])
  else g.coordinates.forEach(function(p){cb(p[0])})
}
function pip(lat,lng,ring){
  var inside=false
  for(var i=0,j=ring.length-1;i<ring.length;j=i++){
    var xi=ring[i][0], yi=ring[i][1], xj=ring[j][0], yj=ring[j][1]
    if(((yi>lat)!==(yj>lat)) && (lng < (xj-xi)*(lat-yi)/(yj-yi)+xi)) inside=true
  }
  return inside
}
function buildGeoIndex(){
  GEO.features.forEach(function(f,i){
    var bb=[999,999,-999,-999]   // [minLng,minLat,maxLng,maxLat]
    eachOuter(f,function(r){r.forEach(function(pt){
      if(pt[0]<bb[0])bb[0]=pt[0]; if(pt[1]<bb[1])bb[1]=pt[1]
      if(pt[0]>bb[2])bb[2]=pt[0]; if(pt[1]>bb[3])bb[3]=pt[1]
    })})
    f._bb=bb; f._i=i
    for(var a=Math.floor(bb[1]/0.02); a<=Math.floor(bb[3]/0.02); a++)
      for(var o=Math.floor(bb[0]/0.02); o<=Math.floor(bb[2]/0.02); o++){
        var k=a+'_'+o; (GEO_IDX[k]=GEO_IDX[k]||[]).push(i)
      }
  })
}
function dongOf(lat,lng){
  var c=GEO_IDX[Math.floor(lat/0.02)+'_'+Math.floor(lng/0.02)]
  if(!c) return -1
  for(var i=0;i<c.length;i++){
    var f=GEO.features[c[i]], bb=f._bb
    if(lng<bb[0]||lat<bb[1]||lng>bb[2]||lat>bb[3]) continue
    var hit=false
    eachOuter(f,function(r){ if(!hit&&pip(lat,lng,r)) hit=true })
    if(hit) return c[i]
  }
  return -1
}
// 색칠 원칙: 신호등 3색으로 전 동을 칠하면 서울 전체가 두드러기처럼 됨(사용자: "징그럽다") →
// 수요 높은 동만 브랜드 보라로 강조하고 낮음·표본부족은 거의 투명. 스팟 파인더 목적에 부합.
function geoStyle(i){
  var s=GEO_STAT[i]
  if(!s||s.on<3) return {c:'#c3c8d4',w:.5,fc:'#94a3b8',fo:.02}   // 표본<3: 사실상 안 칠함
  var occ=s.occ/s.on
  if(occ>=70) return {c:'#4321F3',w:1,fc:'#4321F3',fo:.32}
  if(occ>=55) return {c:'#6d5ef2',w:.8,fc:'#6d5ef2',fo:.20}
  if(occ>=40) return {c:'#8b7dff',w:.6,fc:'#8b7dff',fo:.11}
  return {c:'#c3c8d4',w:.5,fc:'#94a3b8',fo:.03}                  // 예약률 낮음: 색 없음
}
function restyleGeo(){
  if(!GEOPOLYS) return
  GEO.features.forEach(function(f,i){
    var st=geoStyle(i)
    GEOPOLYS[i].forEach(function(pg){
      pg.setOptions({strokeColor:st.c,strokeWeight:st.w,strokeOpacity:.7,fillColor:st.fc,fillOpacity:st.fo})
    })
  })
}
function initGeo(){
  buildGeoIndex()
  FEATS.forEach(function(f){ f.properties.di=dongOf(f.properties.lat,f.properties.lng) })
  GEOPOLYS=GEO.features.map(function(f,i){
    var polys=[]
    eachOuter(f,function(ring){
      var path=ring.map(function(pt){return LL(pt[1],pt[0])})
      var pg=new kakao.maps.Polygon({path:path,strokeWeight:.8,strokeColor:'#64748b',strokeOpacity:.7,
        fillColor:'#94a3b8',fillOpacity:.04})
      kakao.maps.event.addListener(pg,'click',function(){
        var s=GEO_STAT[i]
        if(s&&s.items&&s.items.length) openList(s.items.slice(),'rent')
      })
      polys.push(pg)
    })
    return polys
  })
  applyGeoVisibility()
}
// 1,183개 폴리곤 전부 올리면 팬/줌이 무거움 → 뷰포트(패딩 30%) 밖은 setMap(null) 컬링.
function applyGeoVisibility(){
  if(!GEOPOLYS) return
  var on = show.circles && !recoOn
  var b=viewBox(0.3)
  GEO.features.forEach(function(f,i){
    var bb=f._bb
    var vis = on && !(bb[2]<b.w||bb[0]>b.e||bb[3]<b.s||bb[1]>b.n)
    GEOPOLYS[i].forEach(function(pg){ pg.setMap(vis?map:null) })
  })
}
// 동 라벨: 줌 14+ 에서 폴리곤 내부점에 동명·예약률(렌트 켜면 건수도). 그 미만은 색만.
function renderDongLabels(){
  var wanted=new Map()
  if(show.circles && !recoOn && GEO && zv()>=14){
    var b=viewBox(0.1)
    GEO.features.forEach(function(f){
      var p=f.properties
      if(!b.contains([p.cy,p.cx])) return
      // 키에 렌트 상태 포함 — 렌트 토글 시 라벨(건수 표시)이 diff 렌더에서 새로 그려지게.
      wanted.set('l'+f._i+(show.rent?'r':''), function(){
        var s=GEO_STAT[f._i]||{on:0}
        var occ=s.on?Math.round(s.occ/s.on*10)/10:null
        var h='<div class=dpl><b>'+p.name+'</b>'
        // 렌트 켜면 건수(rcnt)가 뜨므로 표본 수(·on)는 숨김 — "·354 354개" 중복 방지.
        if(occ!=null) h+='<span class=pct style="color:'+occColor(occ)+'">'+occ+'%</span>'+(show.rent?'':'<span class=cnt>·'+s.on+'</span>')
        if(show.rent&&s.n) h+='<span class=rcnt>'+s.n+'개</span>'
        h+='</div>'
        return mkOv(p.cy,p.cx,h,null,5)
      })
    })
  }
  diffRender(shownDlbl, wanted)
}

// ── 수요시설 POI: 왜 이 동네에 수요가 있나(병원 통원·대학 계절학기·산단 출장) ──
var POI_ICON={hospital:'🏥',university:'🎓',industrial:'🏭',academy:'📚',transport:'🚄',tour:'🗼',tourspot:'🗼'}
var POI_CLS={hospital:'h',university:'u',industrial:'i',academy:'a',transport:'t',tour:'g',tourspot:'g'}
function renderPois(){
  var wanted=new Map()
  if(zv()>=12){
    var b=viewBox(0.1), z=zv()
    POIS.forEach(function(p){
      // 종류별 토글: tourspot 은 tour 에 종속. 꺼진 종류는 스킵.
      var grp = p[0]==='tourspot' ? 'tour' : p[0]
      if(!poiShow[grp]) return
      // 개별 관광지(tourspot)는 너무 많아 줌 15+ 에서만. 나머지 수요시설은 줌 12+.
      if(p[0]==='tourspot' && z<15) return
      if(!b.contains([p[2],p[3]])) return
      wanted.set(p[0]+'|'+p[1], function(){
        var op = p[0]==='tourspot' ? 'poi tsp' : 'poi '+POI_CLS[p[0]]
        return mkOv(p[2],p[3],'<div class="'+op+'">'+POI_ICON[p[0]]+' '+p[1]+'</div>',null,40)
      })
    })
  }
  diffRender(shownPoi, wanted)
}

// ── ⭐ 추천 스팟: 수요근거는 많은데 단기임대 공급 없는 동 + 근처 부동산 매물 ──
var RECO=null, recoKey=null, recoShapes=[]
// 추천 매물 매칭에 적용할 필터 → 쿼리스트링(유형 칩·보증금 상한). 수요점수는 서버에서 캐시 재사용.
function recoParams(){
  var p=new URLSearchParams()
  if(filt.btype && NAV_TYPE[filt.btype]) p.set('btype', NAV_TYPE[filt.btype])
  if(filt.dep) p.set('max_dep', filt.dep)
  return p.toString()
}
function clearReco(){ recoShapes.forEach(function(s){s.setMap(null)}); recoShapes=[] }
function loadReco(){
  clearReco()
  if(!recoOn) return
  var key=recoParams()
  if(RECO && key===recoKey){ drawReco(); return }   // 같은 조건이면 재요청 없이 재사용
  recoKey=key
  document.getElementById('stat').textContent='추천 스팟 분석 중…'
  fetch('/samsam/api/recommend'+(key?'?'+key:''),{credentials:'same-origin'}).then(function(r){return r.json()})
    .then(function(d){ RECO=d.spots||[]; drawReco(); document.getElementById('stat').textContent='⭐ 추천 스팟 '+RECO.length+'곳 (수요근거 많은데 단기임대 없는 동)' })
    .catch(function(){ document.getElementById('stat').textContent='추천 로드 실패' })
}
function drawReco(){
  clearReco()
  if(!recoOn||!RECO) return
  // 점수 정규화 → 원 크기·강조
  var maxS=Math.max.apply(null, RECO.map(function(s){return s.score})) || 1
  RECO.forEach(function(s){
    var r=340+Math.round(s.score/maxS*320)
    var c=new kakao.maps.Circle({center:LL(s.lat,s.lon),radius:r,strokeWeight:2,strokeColor:'#ca8a04',
      strokeOpacity:.9,fillColor:'#facc15',fillOpacity:.22})
    c.setMap(map); recoShapes.push(c)
    var o=mkOv(s.lat,s.lon,
      '<div class="reco-badge">⭐ '+s.dong+'<br><b>'+s.score+'점</b><span class=reco-n> 매물'+(s.n_listings!=null?s.n_listings:s.listings.length)+'</span></div>',
      function(){ openReco(s) },50)
    o.setMap(map); recoShapes.push(o)
  })
}
function openReco(s){
  var t=document.getElementById('mtitle'), b=document.getElementById('mbody')
  t.textContent='⭐ '+s.sigungu+' '+s.dong+' — 추천 '+s.score+'점'
  var poi=(s.poi||[]).map(function(p){
    var ic={hospital:'🏥',university:'🎓',industrial:'🏭',academy:'📚',transport:'🚄',tour:'🗼'}[p.kind]||'📍'
    return ic+' '+p.name+' '+(p.dist_m/1000).toFixed(1)+'km' }).join(' · ')
  var h='<div style="padding:10px 8px;font-size:12.5px;color:#475569;border-bottom:1px solid #eef0f2">'+
    '단기임대 <b>'+s.n_samsam+'개뿐</b> · 월세 회전율 <b>'+s.turnover+'%</b>'+
    (s.workers ? ' · 종사자 <b>'+s.workers.toLocaleString()+'명</b>' : '')+
    (s.wealth ? ' · 소비력 <b>아파트보증금 '+(s.wealth>=10000?(s.wealth/10000).toFixed(1)+'억':s.wealth+'만')+'</b>' : '')+'<br>'+
    '<span style="color:#ca8a04;font-weight:700">'+poi+'</span></div>'+
    '<div style="padding:8px;font-size:12px;color:#64748b">여기서 시작할 만한 부동산 매물 <b>'+(s.n_listings!=null?s.n_listings:s.listings.length)+'건</b>'+
      ((s.n_listings!=null&&s.n_listings>s.listings.length)?' <span style="color:#94a3b8">(월세 싼 순 '+s.listings.length+'건 표시)</span>':'')+' ↓</div>'
  s.listings.forEach(function(m){
    h+='<a class=item href="'+m.url+'" target=_blank rel=noreferrer>'+
      '<div><div class=it-name>'+(m.name||'(이름없음)')+'</div>'+
      '<div class=it-sub>월세 '+(m.rent!=null?m.rent:'-')+'만 / 보증금 '+(m.dep!=null?m.dep.toLocaleString():'-')+'만'+
      (m.m2?' · '+m.m2+'㎡':'')+(m.floor!=null?' · '+m.floor+'층':'')+'</div></div>'+
      '<div class=it-go>매물 보기 →</div></a>'
  })
  if(!s.listings.length) h+='<div style="padding:14px;color:#94a3b8">근처 조건 맞는 부동산 매물이 아직 없어요.</div>'
  b.innerHTML=h
  document.getElementById('modal').style.display='flex'
}

// ── 부동산(네이버): 줌 15+ bbox 조회, 유형 필터 반영 ──
var navTimer=null, navSeq=0
function clearNav(){ shownNav.forEach(function(o){o.setMap(null)}); shownNav=[] }
function loadNaver(){
  if(recoOn||!show.naver||zv()<NAVER_ZOOM){clearNav();setStat();return}
  var b=viewBox(0), seq=++navSeq
  var p=new URLSearchParams({min_lat:b.s,max_lat:b.n,min_lng:b.w,max_lng:b.e,rent:'0'})
  fetch('/samsam/api/map?'+p.toString(),{credentials:'same-origin'})
    .then(function(r){return r.json()}).then(function(d){
      if(seq!==navSeq) return
      clearNav()
      var want=filt.btype?NAV_TYPE[filt.btype]:null, cnt=0
      ;(d.naver||[]).forEach(function(n){
        if(want && n.btcode!==want) return
        cnt++
        var o=mkOv(n.lat,n.lng,
          '<div style="transform:translate(-50%,-50%)"><div class="pin nv"></div></div>',
          function(){openList([n],'nv')},8)
        o.setMap(map); shownNav.push(o)
      })
      setStat(cnt)
    }).catch(function(){setStat()})
}
function setStat(navN){
  var z=zv()
  var f=(filt.btype||'전체')+(filt.week?' · 주당'+filt.week+'만↑':'')+(filt.net?' · 순수익'+filt.net+'만↑':'')+(filt.occ?' · 예약률'+filt.occ+'%↑':'')
  var s='렌트 '+rentShown.toLocaleString()+'개 ['+f+']'
  s+= z<NAVER_ZOOM ? ' · 확대하면 부동산' : ' · 부동산 '+(navN!=null?navN:0)+'개'
  document.getElementById('stat').textContent=s
}

// ── 검색: 역 → 동 순으로 부분일치, 첫 결과로 이동 ──
function doSearch(){
  var q=(document.getElementById('q').value||'').trim()
  if(!q) return
  var qq=q.replace(/역$/,'')
  for(var i=0;i<STATIONS.length;i++){
    if(STATIONS[i][0].indexOf(qq)>=0){setViewZ(STATIONS[i][1],STATIONS[i][2],15);return}
  }
  if(GEO){ for(var j=0;j<GEO.features.length;j++){
    var gp=GEO.features[j].properties
    if(gp.name.indexOf(q)>=0){setViewZ(gp.cy,gp.cx,15);return}
  }}
  for(var k=0;k<FEATS.length;k++){   // 필터로 없어진 동도 전체 매물에서 검색
    var p=FEATS[k].properties
    if(p.dong&&p.dong.indexOf(q)>=0){setViewZ(p.lat,p.lng,15);return}
  }
  document.getElementById('stat').textContent='"'+q+'" 검색 결과 없음'
}
document.getElementById('q').addEventListener('keydown',function(e){if(e.key==='Enter')doSearch()})

// ── 지도 생성 + 초기 1회 로드: 매물 + 동 경계(정적, 7일 캐시) 병렬 ──
kakao.maps.load(function(){
  map=new kakao.maps.Map(document.getElementById('map'),{center:LL(37.5665,126.978),level:7})
  map.addControl(new kakao.maps.ZoomControl(), kakao.maps.ControlPosition.LEFT)
  Promise.all([
    fetch('/samsam/api/map_all',{credentials:'same-origin'}).then(function(r){return r.json()}),
    fetch('/dong_geo.json').then(function(r){return r.json()}).catch(function(){return null})
  ]).then(function(res){
    var d=res[0]
    STATIONS=d.stations||[]
    POIS=d.pois||[]
    FEATS=(d.rent||[]).filter(function(a){return a[1]>=33&&a[1]<=39.5&&a[2]>=124&&a[2]<=132})
      .map(function(a){return {type:'Feature',geometry:{type:'Point',coordinates:[a[2],a[1]]},
        properties:{id:a[0],lat:a[1],lng:a[2],occ:a[3],week:a[4],pyeong:a[5],btype:a[6],name:a[7],sigungu:a[8],dong:a[9],net:a[10]}}})
    GEO=res[1]
    if(GEO) initGeo()   // 경계 로드 실패 시에도 나머지 지도는 동작
    applyFilter(); renderPois()
  }).catch(function(){document.getElementById('stat').textContent='로드 실패 — 새로고침 해주세요'})
  // 카카오맵은 팬/줌 종료 시 idle 발생 — 그때만 다시 그림(연속 move 스로틀 불필요).
  kakao.maps.event.addListener(map,'idle',function(){
    renderRent(); renderDongLabels(); renderPois(); applyGeoVisibility()
    clearTimeout(navTimer); navTimer=setTimeout(loadNaver,300)
  })
})

// 필터 이벤트
document.querySelectorAll('#chips .chip').forEach(function(ch){
  ch.addEventListener('click',function(){
    document.querySelectorAll('#chips .chip').forEach(function(x){x.classList.remove('on')})
    ch.classList.add('on'); filt.btype=ch.dataset.t||''; applyFilter(); if(recoOn) loadReco()
  })
})
;['week','occ','net'].forEach(function(k){
  var el=document.getElementById('f_'+k), tm=null
  el.addEventListener('input',function(){
    clearTimeout(tm); tm=setTimeout(function(){filt[k]=parseFloat(el.value)||0; applyFilter()},400)
  })
})
// 보증금 상한 — ⭐ 추천 스팟 매물 매칭에만 적용(수요점수 무관, 서버 캐시 재사용).
;(function(){
  var el=document.getElementById('f_dep'), tm=null
  el.addEventListener('input',function(){
    clearTimeout(tm); tm=setTimeout(function(){ filt.dep=parseFloat(el.value)||0; if(recoOn) loadReco() },400)
  })
})()
;['circles','rent','naver'].forEach(function(k){
  document.getElementById('t_'+k).addEventListener('click',function(){
    show[k]=!show[k]; this.classList.toggle('on',show[k])
    if(k==='circles'){ applyGeoVisibility(); renderDongLabels(); renderRent() }
    else if(k==='rent'){ renderRent(); renderDongLabels() }
    else loadNaver()
  })
})
// 수요시설 종류별 토글
document.querySelectorAll('#poirow .ptog').forEach(function(el){
  el.addEventListener('click',function(){
    var k=el.dataset.k; poiShow[k]=!poiShow[k]; el.classList.toggle('on',poiShow[k]); renderPois()
  })
})
// ⭐ 추천만 보기: 켜면 렌트/폴리곤/부동산 숨기고 추천 스팟만, 끄면 원복
document.getElementById('t_reco').addEventListener('click',function(){
  recoOn=!recoOn; this.classList.toggle('on',recoOn)
  renderRent(); renderDongLabels(); applyGeoVisibility(); loadNaver()
  if(recoOn) loadReco()
  else { clearReco(); setStat() }
})
</script>
</body></html>"""
