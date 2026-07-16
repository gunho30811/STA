# -*- coding: utf-8 -*-
"""전용 풀스크린 지도(/map) 페이지 템플릿.

v4 — 필터/검색 추가 + 역삼동 오표시 수정:
- 좌측 상단 필터: 건물유형 칩(6종) · 검색(동/역 → 이동) · 주당 n만원↑ · 예약률 n%↑.
  필터 변경 시 supercluster 인덱스 재구축(22k ~100ms) + 동 원 재계산 → 렌트·원·부동산에 적용.
- 동 원을 클라이언트에서 계산하되 **중앙값 좌표** 사용 — lng=0 같은 불량 좌표가 평균을 끌고 가
  역삼동 원이 부천 쪽에 그려지던 버그 수정(서버 map_all도 한국 밖 좌표 제외).
- supercluster + diff 렌더(줌 ~20ms·배지 깜빡임 0)는 v3 그대로.
"""

MAP_PAGE = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>rendit · 지도</title>
<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard-dynamic-subset.css">
<link rel=stylesheet href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
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
.occ-badge-wrap{background:none;border:none}
.occ-badge{transform:translate(-50%,-50%);background:rgba(255,255,255,.9);border:1px solid;border-radius:8px;
padding:2px 6px;font-size:10px;font-weight:700;text-align:center;line-height:1.2;white-space:nowrap;
box-shadow:0 1px 3px rgba(0,0,0,.15)}
.occ-badge b{font-size:12px}.occ-n{font-weight:500;color:#94a3b8;font-size:9px}
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
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/supercluster@8.0.1/dist/supercluster.min.js"></script>
<script>
var NAVER_ZOOM=15, CIRCLE_ZOOM=11, LIST_MAX=60
var NAV_TYPE={'오피스텔':'OPST','아파트':'APT','연립빌라':'VL','단독주택':'DDDGG','원룸건물':'OR','상가주택':'SG'}
function occColor(o){return o==null?'#94a3b8':o>=60?'#059669':o>=30?'#f59e0b':'#dc2626'}
function occCls(o){return o==null?'mut':o>=60?'good':o>=30?'midc':'bad'}
function median(arr){var a=arr.slice().sort(function(x,y){return x-y});var m=a.length>>1;return a.length%2?a[m]:(a[m-1]+a[m])/2}

var map=L.map('map',{center:[37.5665,126.978],zoom:13})
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap'}).addTo(map)

var rentLayer=L.layerGroup().addTo(map)
var circleLayer=L.layerGroup().addTo(map)
var navLayer=L.layerGroup().addTo(map)
var poiLayer=L.layerGroup().addTo(map)
// 첫인상 정리: 동 예약률만 켜고 시작. 렌트/부동산/수요시설은 사용자가 필요할 때 켠다.
var show={circles:true,rent:false,naver:false}
var poiShow={hospital:false,university:false,industrial:false,academy:false,transport:false,tour:false}
var FEATS=[], STATIONS=[], POIS=[], idx=null, CIRCLES=[], rentShown=0
var filt={btype:'',week:0,occ:0,net:0}
var shownRent=new Map(), shownCirc=new Map()

// ── 필터 적용: 인덱스 재구축(22k ~100ms) + 동 원 재계산(중앙값 좌표) ──
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
  // 동 원(지역 수요 지표)은 매물 필터와 무관하게 항상 '전체' 기준 — 예약률 50%↑ 필터를 걸면
  // 남은 매물 평균이라 모든 동이 높아 보이는 선택 편향 방지. 유형 칩만 세그먼트로 반영.
  var fsCirc = filt.btype ? FEATS.filter(function(f){return f.properties.btype===filt.btype}) : FEATS
  var g={}
  fsCirc.forEach(function(f){
    var p=f.properties, k=p.sigungu+'|'+p.dong
    if(!g[k]) g[k]={lats:[],lngs:[],occ:0,n:0,dong:p.dong}
    g[k].lats.push(p.lat); g[k].lngs.push(p.lng); g[k].occ+=(p.occ||0); g[k].n++
  })
  CIRCLES=[]
  Object.keys(g).forEach(function(k){
    var v=g[k]   // 매물 1개뿐인 동도 표기(요청: 동이면 다 보이게). 표본 수(·n)로 신뢰도 판단.
    CIRCLES.push([median(v.lats),median(v.lngs),Math.round(v.occ/v.n*10)/10,v.n,v.dong,k])
  })
  shownRent.forEach(function(ly){rentLayer.removeLayer(ly)}); shownRent.clear()
  shownCirc.forEach(function(ly){circleLayer.removeLayer(ly)}); shownCirc.clear()
  renderRent(); renderCircles(); loadNaver(); setStat()
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
        (r.net!=null?' · <b style=\"color:'+(r.net>=0?'#059669':'#dc2626')+'\">월순수익 '+r.net+'만</b>':'')+
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

// ── diff 렌더(깜빡임 방지) ──
function diffRender(group, shownMap, wanted){
  shownMap.forEach(function(ly,key){ if(!wanted.has(key)){group.removeLayer(ly);shownMap.delete(key)} })
  wanted.forEach(function(make,key){
    if(shownMap.has(key)) return
    var ly=make(); shownMap.set(key,ly); group.addLayer(ly)
  })
}

function renderRent(){
  if(!idx) return
  var wanted=new Map()
  if(show.rent){
    var b=map.getBounds(), z=Math.round(map.getZoom())
    var items=idx.getClusters([b.getWest()-0.02,b.getSouth()-0.02,b.getEast()+0.02,b.getNorth()+0.02], z)
    items.forEach(function(f){
      var lng=f.geometry.coordinates[0], lat=f.geometry.coordinates[1]
      if(f.properties.cluster){
        var cid=f.properties.cluster_id, n=f.properties.point_count
        wanted.set('c'+cid+'_'+z, function(){
          var d=n>=1000?58:n>=100?52:n>=30?44:n>=10?38:32
          var txt=n>=1000?(Math.round(n/100)/10)+'k':n
          var m=L.marker([lat,lng],{icon:L.divIcon({className:'',iconSize:[d,d],
            html:'<div class="clus rent" style="width:'+d+'px;height:'+d+'px;font-size:13px">'+txt+'<small>개</small></div>'})})
          m.on('click',function(){
            if(n>LIST_MAX){map.setView([lat,lng],Math.min(idx.getClusterExpansionZoom(cid),18))}
            else{openList(idx.getLeaves(cid,Infinity).map(function(l){return l.properties}),'rent')}
          })
          return m
        })
      }else{
        var r=f.properties
        wanted.set('p'+r.id, function(){
          var m=L.marker([lat,lng],{icon:L.divIcon({className:'',iconSize:[16,16],
            html:'<div class=pin style="background:'+occColor(r.occ)+'"></div>'})})
          m.on('click',function(){openList([r],'rent')})
          return m
        })
      }
    })
  }
  diffRender(rentLayer, shownRent, wanted)
}

function renderCircles(){
  var wanted=new Map()
  if(show.circles && map.getZoom()>=CIRCLE_ZOOM){
    var b=map.getBounds().pad(0.1), z=map.getZoom()
    // 줌 14 미만: 글자 배지가 화면을 덮어 지저분 → 예약률 '점'만(색으로 수준 표현).
    // 줌 14+: 동명·예약률 배지 표시(그 정도로 확대하면 화면당 동 수가 적어 안 겹침).
    var showBadge = z>=14
    CIRCLES.forEach(function(c){
      if(!b.contains([c[0],c[1]])) return
      wanted.set(c[5]+(showBadge?'b':'d'), function(){
        if(!showBadge){
          return L.circleMarker([c[0],c[1]],{radius:6,color:'#fff',weight:1,
            fillColor:occColor(c[2]),fillOpacity:.75,interactive:false})
        }
        var g=L.layerGroup()
        L.circle([c[0],c[1]],{radius:420,color:occColor(c[2]),weight:1.2,fillColor:occColor(c[2]),fillOpacity:.10,interactive:false}).addTo(g)
        L.marker([c[0],c[1]],{interactive:false,icon:L.divIcon({className:'occ-badge-wrap',iconSize:null,
          html:'<div class="occ-badge" style="border-color:'+occColor(c[2])+';color:'+occColor(c[2])+'">'+c[4]+'<br><b>'+c[2]+'%</b><span class=occ-n>·'+c[3]+'</span></div>'})}).addTo(g)
        return g
      })
    })
  }
  diffRender(circleLayer, shownCirc, wanted)
}

// ── 수요시설 POI: 왜 이 동네에 수요가 있나(병원 통원·대학 계절학기·산단 출장) ──
var shownPoi=new Map()
var POI_ICON={hospital:'🏥',university:'🎓',industrial:'🏭',academy:'📚',transport:'🚄',tour:'🗼',tourspot:'🗼'}
var POI_CLS={hospital:'h',university:'u',industrial:'i',academy:'a',transport:'t',tour:'g',tourspot:'g'}
function renderPois(){
  var wanted=new Map()
  if(map.getZoom()>=12){
    var b=map.getBounds().pad(0.1), z=map.getZoom()
    POIS.forEach(function(p){
      // 종류별 토글: tourspot 은 tour 에 종속. 꺼진 종류는 스킵.
      var grp = p[0]==='tourspot' ? 'tour' : p[0]
      if(!poiShow[grp]) return
      // 개별 관광지(tourspot)는 너무 많아 줌 15+ 에서만. 나머지 수요시설은 줌 12+.
      if(p[0]==='tourspot' && z<15) return
      if(!b.contains([p[2],p[3]])) return
      wanted.set(p[0]+'|'+p[1], function(){
        var op = p[0]==='tourspot' ? 'poi tsp' : 'poi '+POI_CLS[p[0]]
        return L.marker([p[2],p[3]],{zIndexOffset:500,icon:L.divIcon({className:'',iconSize:null,
          html:'<div class="'+op+'">'+POI_ICON[p[0]]+' '+p[1]+'</div>'})})
      })
    })
  }
  diffRender(poiLayer, shownPoi, wanted)
}

// ── ⭐ 추천 스팟: 수요근거는 많은데 단기임대 공급 없는 동 + 근처 부동산 매물 ──
var recoLayer=L.layerGroup().addTo(map)
var RECO=null, recoOn=false
function loadReco(){
  recoLayer.clearLayers()
  if(!recoOn) return
  if(RECO){ drawReco(); return }
  document.getElementById('stat').textContent='추천 스팟 분석 중…'
  fetch('/samsam/api/recommend',{credentials:'same-origin'}).then(function(r){return r.json()})
    .then(function(d){ RECO=d.spots||[]; drawReco(); document.getElementById('stat').textContent='⭐ 추천 스팟 '+RECO.length+'곳 (수요근거 많은데 단기임대 없는 동)' })
    .catch(function(){ document.getElementById('stat').textContent='추천 로드 실패' })
}
function drawReco(){
  recoLayer.clearLayers()
  if(!recoOn||!RECO) return
  // 점수 정규화 → 원 크기·강조
  var maxS=Math.max.apply(null, RECO.map(function(s){return s.score})) || 1
  RECO.forEach(function(s){
    var r=340+Math.round(s.score/maxS*320)
    L.circle([s.lat,s.lon],{radius:r,color:'#ca8a04',weight:2,fillColor:'#facc15',fillOpacity:.22}).addTo(recoLayer)
    var mk=L.marker([s.lat,s.lon],{icon:L.divIcon({className:'',iconSize:null,
      html:'<div class="reco-badge">⭐ '+s.dong+'<br><b>'+s.score+'점</b><span class=reco-n> 매물'+s.listings.length+'</span></div>'})})
    mk.on('click',function(){ openReco(s) }); mk.addTo(recoLayer)
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
    '<div style="padding:8px;font-size:12px;color:#64748b">여기서 시작할 만한 부동산 매물 '+s.listings.length+'건 ↓</div>'
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
function loadNaver(){
  if(!show.naver||map.getZoom()<NAVER_ZOOM){navLayer.clearLayers();setStat();return}
  var b=map.getBounds(), seq=++navSeq
  var p=new URLSearchParams({min_lat:b.getSouth(),max_lat:b.getNorth(),min_lng:b.getWest(),max_lng:b.getEast(),rent:'0'})
  fetch('/samsam/api/map?'+p.toString(),{credentials:'same-origin'})
    .then(function(r){return r.json()}).then(function(d){
      if(seq!==navSeq) return
      navLayer.clearLayers()
      var want=filt.btype?NAV_TYPE[filt.btype]:null, cnt=0
      ;(d.naver||[]).forEach(function(n){
        if(want && n.btcode!==want) return
        cnt++
        var m=L.marker([n.lat,n.lng],{icon:L.divIcon({className:'',iconSize:[13,13],html:'<div class="pin nv"></div>'})})
        m.on('click',function(){openList([n],'nv')})
        navLayer.addLayer(m)
      })
      setStat(cnt)
    }).catch(function(){setStat()})
}
function setStat(navN){
  var z=map.getZoom()
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
    if(STATIONS[i][0].indexOf(qq)>=0){map.setView([STATIONS[i][1],STATIONS[i][2]],15);return}
  }
  for(var j=0;j<CIRCLES.length;j++){
    if(CIRCLES[j][4].indexOf(q)>=0){map.setView([CIRCLES[j][0],CIRCLES[j][1]],15);return}
  }
  for(var k=0;k<FEATS.length;k++){   // 필터로 원이 사라진 동도 전체 매물에서 검색
    var p=FEATS[k].properties
    if(p.dong&&p.dong.indexOf(q)>=0){map.setView([p.lat,p.lng],15);return}
  }
  document.getElementById('stat').textContent='"'+q+'" 검색 결과 없음'
}
document.getElementById('q').addEventListener('keydown',function(e){if(e.key==='Enter')doSearch()})

// ── 초기 1회 로드 ──
fetch('/samsam/api/map_all',{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(d){
  STATIONS=d.stations||[]
  POIS=d.pois||[]
  FEATS=(d.rent||[]).filter(function(a){return a[1]>=33&&a[1]<=39.5&&a[2]>=124&&a[2]<=132})
    .map(function(a){return {type:'Feature',geometry:{type:'Point',coordinates:[a[2],a[1]]},
      properties:{id:a[0],lat:a[1],lng:a[2],occ:a[3],week:a[4],pyeong:a[5],btype:a[6],name:a[7],sigungu:a[8],dong:a[9],net:a[10]}}})
  applyFilter(); renderPois()
}).catch(function(){document.getElementById('stat').textContent='로드 실패 — 새로고침 해주세요'})

// 이동 중 스로틀 갱신 + 끝나면 확정, 네이버 디바운스
var mvTimer=null
map.on('move',function(){
  if(mvTimer) return
  mvTimer=setTimeout(function(){mvTimer=null;renderRent();renderCircles()},120)
})
map.on('moveend zoomend',function(){
  renderRent(); renderCircles(); renderPois()
  clearTimeout(navTimer); navTimer=setTimeout(loadNaver,400)
})

// 필터 이벤트
document.querySelectorAll('#chips .chip').forEach(function(ch){
  ch.addEventListener('click',function(){
    document.querySelectorAll('#chips .chip').forEach(function(x){x.classList.remove('on')})
    ch.classList.add('on'); filt.btype=ch.dataset.t||''; applyFilter()
  })
})
;['week','occ','net'].forEach(function(k){
  var el=document.getElementById('f_'+k), tm=null
  el.addEventListener('input',function(){
    clearTimeout(tm); tm=setTimeout(function(){filt[k]=parseFloat(el.value)||0; applyFilter()},400)
  })
})
;['circles','rent','naver'].forEach(function(k){
  document.getElementById('t_'+k).addEventListener('click',function(){
    show[k]=!show[k]; this.classList.toggle('on',show[k])
    if(k==='circles') renderCircles()
    else if(k==='rent') renderRent()
    else loadNaver()
  })
})
// 수요시설 종류별 토글
document.querySelectorAll('#poirow .ptog').forEach(function(el){
  el.addEventListener('click',function(){
    var k=el.dataset.k; poiShow[k]=!poiShow[k]; el.classList.toggle('on',poiShow[k]); renderPois()
  })
})
// ⭐ 추천만 보기: 켜면 렌트/원/부동산 숨기고 추천 스팟만, 끄면 원복
document.getElementById('t_reco').addEventListener('click',function(){
  recoOn=!recoOn; this.classList.toggle('on',recoOn)
  if(recoOn){
    map.removeLayer(rentLayer); map.removeLayer(circleLayer); map.removeLayer(navLayer)
    loadReco()
  }else{
    map.addLayer(rentLayer); map.addLayer(circleLayer); map.addLayer(navLayer)
    recoLayer.clearLayers(); setStat()
  }
})
</script>
</body></html>"""
