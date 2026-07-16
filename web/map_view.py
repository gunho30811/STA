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
.occ-badge{transform:translate(-50%,-50%);background:rgba(255,255,255,.93);border:1.5px solid;border-radius:10px;
padding:3px 8px;font-size:11px;font-weight:700;text-align:center;line-height:1.25;white-space:nowrap;
box-shadow:0 1px 4px rgba(0,0,0,.2)}
.occ-badge b{font-size:13px}.occ-n{font-weight:500;color:#94a3b8;font-size:10px}
.pin{width:16px;height:16px;border-radius:50%;border:2.5px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35)}
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
    <label class="tog on" id=t_rent><span class=dot style="background:#4321F3"></span>렌트</label>
    <label class="tog on" id=t_naver><span class=dot style="background:#14b8a6"></span>부동산</label>
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
var show={circles:true,rent:true,naver:true}
var FEATS=[], STATIONS=[], idx=null, CIRCLES=[], rentShown=0
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
  // 동 원: sigungu+dong 그룹 → 중앙값 좌표(불량 좌표 내성) + 평균 예약률
  var g={}
  fs.forEach(function(f){
    var p=f.properties, k=p.sigungu+'|'+p.dong
    if(!g[k]) g[k]={lats:[],lngs:[],occ:0,n:0,dong:p.dong}
    g[k].lats.push(p.lat); g[k].lngs.push(p.lng); g[k].occ+=(p.occ||0); g[k].n++
  })
  CIRCLES=[]
  Object.keys(g).forEach(function(k){
    var v=g[k]; if(v.n<3) return
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
    var b=map.getBounds().pad(0.1)
    CIRCLES.forEach(function(c){
      if(!b.contains([c[0],c[1]])) return
      wanted.set(c[5], function(){
        var g=L.layerGroup()
        L.circle([c[0],c[1]],{radius:420,color:occColor(c[2]),weight:1.5,fillColor:occColor(c[2]),fillOpacity:.13,interactive:false}).addTo(g)
        L.marker([c[0],c[1]],{interactive:false,icon:L.divIcon({className:'occ-badge-wrap',iconSize:null,
          html:'<div class="occ-badge" style="border-color:'+occColor(c[2])+';color:'+occColor(c[2])+'">'+c[4]+'<br><b>'+c[2]+'%</b><span class=occ-n>·'+c[3]+'</span></div>'})}).addTo(g)
        return g
      })
    })
  }
  diffRender(circleLayer, shownCirc, wanted)
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
  FEATS=(d.rent||[]).filter(function(a){return a[1]>=33&&a[1]<=39.5&&a[2]>=124&&a[2]<=132})
    .map(function(a){return {type:'Feature',geometry:{type:'Point',coordinates:[a[2],a[1]]},
      properties:{id:a[0],lat:a[1],lng:a[2],occ:a[3],week:a[4],pyeong:a[5],btype:a[6],name:a[7],sigungu:a[8],dong:a[9],net:a[10]}}})
  applyFilter()
}).catch(function(){document.getElementById('stat').textContent='로드 실패 — 새로고침 해주세요'})

// 이동 중 스로틀 갱신 + 끝나면 확정, 네이버 디바운스
var mvTimer=null
map.on('move',function(){
  if(mvTimer) return
  mvTimer=setTimeout(function(){mvTimer=null;renderRent();renderCircles()},120)
})
map.on('moveend zoomend',function(){
  renderRent(); renderCircles()
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
</script>
</body></html>"""
