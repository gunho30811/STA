# -*- coding: utf-8 -*-
"""전용 풀스크린 지도(/map) 페이지 템플릿.

/samsam/api/map 을 그대로 사용(렌트·부동산 bbox 매물 + 동별 예약률 원).
- Leaflet + markercluster(CDN): 겹치는 매물은 'n개' 클러스터 버블로 묶음(렌트 보라·부동산 청록).
- 렌트 마커 색 = 예약률(초록 60%↑/주황/빨강 30%↓), 부동산은 청록 단색.
- 마커/클러스터 클릭 → 하단 모달로 매물 리스트업(렌트는 각 매물 예약률 표시, 링크 이동).
  클러스터가 60개 초과면 리스트 대신 줌인.
"""

MAP_PAGE = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>rendit · 지도</title>
<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard-dynamic-subset.css">
<link rel=stylesheet href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<link rel=stylesheet href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css">
<style>
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;font-family:"Pretendard","Malgun Gothic",sans-serif;display:flex;flex-direction:column;background:#0f172a}
#map{flex:1;min-height:0}
.bar{position:absolute;z-index:1000;top:12px;left:54px;right:12px;display:flex;gap:6px;flex-wrap:wrap;align-items:center;pointer-events:none}
.bar>*{pointer-events:auto}
.tog{display:inline-flex;align-items:center;gap:5px;padding:6px 12px;border:1.5px solid #cbd5e1;border-radius:999px;
font-size:12.5px;font-weight:700;color:#94a3b8;background:rgba(255,255,255,.95);cursor:pointer;user-select:none;
box-shadow:0 1px 4px rgba(0,0,0,.2)}
.tog input{display:none}
.tog.on{color:#111827}
.tog.on .dot{opacity:1}
.dot{width:9px;height:9px;border-radius:50%;opacity:.35}
.stat{font-size:11.5px;color:#e2e8f0;background:rgba(15,23,42,.75);padding:6px 11px;border-radius:8px}
/* 동별 예약률 배지 */
.occ-badge-wrap{background:none;border:none}
.occ-badge{transform:translate(-50%,-50%);background:rgba(255,255,255,.93);border:1.5px solid;border-radius:10px;
padding:3px 8px;font-size:11px;font-weight:700;text-align:center;line-height:1.25;white-space:nowrap;
box-shadow:0 1px 4px rgba(0,0,0,.2)}
.occ-badge b{font-size:13px}.occ-n{font-weight:500;color:#94a3b8;font-size:10px}
/* 매물 점(단일) — 예약률 색, 균일 크기 */
.pin{width:16px;height:16px;border-radius:50%;border:2.5px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35)}
.pin.nv{width:13px;height:13px;background:#14b8a6}
/* 클러스터 버블 — n개 */
.clus{display:flex;align-items:center;justify-content:center;border-radius:50%;color:#fff;font-weight:800;
border:3px solid rgba(255,255,255,.85);box-shadow:0 2px 8px rgba(0,0,0,.35);line-height:1.1;text-align:center}
.clus.rent{background:#4321F3}.clus.nv{background:#0d9488}
.clus small{font-weight:600;font-size:9px;display:block}
/* 매물 리스트 모달 */
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
</style></head><body>
<div id=map></div>
<div class=bar>
  <label class="tog on" id=t_circles><span class=dot style="background:#059669"></span>동별 예약률</label>
  <label class="tog on" id=t_rent><span class=dot style="background:#4321F3"></span>렌트</label>
  <label class="tog on" id=t_naver><span class=dot style="background:#14b8a6"></span>부동산</label>
  <span class=stat id=stat>불러오는 중…</span>
</div>
<div class=mback id=modal style="display:none" onclick="closeModal(event)">
  <div class=mcard onclick="event.stopPropagation()">
    <div class=mhead><div class=mtitle id=mtitle></div><button class=mx onclick="closeModal()">✕</button></div>
    <div class=mbody id=mbody></div>
  </div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<script>
var MARKER_ZOOM=14, LIST_MAX=60
function occColor(o){return o==null?'#94a3b8':o>=60?'#059669':o>=30?'#f59e0b':'#dc2626'}
function occCls(o){return o==null?'mut':o>=60?'good':o>=30?'midc':'bad'}

var map=L.map('map',{center:[37.5665,126.978],zoom:13})
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap'}).addTo(map)

function clusterIcon(cls){return function(c){
  var n=c.getChildCount(), d=n>=100?52:n>=30?44:n>=10?38:32
  return L.divIcon({html:'<div class="clus '+cls+'" style="width:'+d+'px;height:'+d+'px;font-size:'+(n>=100?14:13)+'px">'+n+'<small>개</small></div>',
    className:'', iconSize:[d,d]})
}}
function makeCluster(cls){
  var g=L.markerClusterGroup({iconCreateFunction:clusterIcon(cls),showCoverageOnHover:false,
    zoomToBoundsOnClick:false,maxClusterRadius:44,disableClusteringAtZoom:18})
  g.on('clusterclick',function(e){
    var ms=e.layer.getAllChildMarkers()
    if(ms.length>LIST_MAX){map.setView(e.layer.getLatLng(),Math.min(map.getZoom()+2,18));return}
    openList(ms.map(function(m){return m.__d}), cls)
  })
  return g
}
var circles=L.layerGroup().addTo(map)
var rentC=makeCluster('rent').addTo(map)
var navC=makeCluster('nv').addTo(map)
var show={circles:true,rent:true,naver:true}

function openList(items,cls){
  var t=document.getElementById('mtitle'), b=document.getElementById('mbody'), h=''
  if(cls==='rent'){
    items.sort(function(a,z){return (z.occ||0)-(a.occ||0)})
    t.textContent='렌트 매물 '+items.length+'개 (예약률순)'
    items.forEach(function(r){
      h+='<a class=item href="'+(r.url||'#')+'" target=_blank rel=noreferrer>'+
        '<div><div class=it-name>'+(r.name||'(이름없음)')+'</div>'+
        '<div class=it-sub>'+(r.btype||'')+' · '+(r.pyeong!=null?r.pyeong+'평':'-')+' · 주당 '+(r.week!=null?r.week+'만':'-')+'</div></div>'+
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

var timer=null
map.on('moveend zoomend',function(){clearTimeout(timer);timer=setTimeout(load,350)})

function load(){
  var b=map.getBounds(), z=map.getZoom()
  var p=new URLSearchParams({min_lat:b.getSouth(),max_lat:b.getNorth(),min_lng:b.getWest(),max_lng:b.getEast()})
  if(z<MARKER_ZOOM){p.set('rent','0');p.set('naver','0')}
  fetch('/samsam/api/map?'+p.toString(),{credentials:'same-origin'})
    .then(function(r){return r.json()}).then(function(d){
    circles.clearLayers()
    if(show.circles)(d.circles||[]).forEach(function(c){
      L.circle([c.lat,c.lng],{radius:420,color:occColor(c.occ),weight:1.5,fillColor:occColor(c.occ),fillOpacity:.13,interactive:false}).addTo(circles)
      L.marker([c.lat,c.lng],{interactive:false,icon:L.divIcon({className:'occ-badge-wrap',iconSize:null,
        html:'<div class="occ-badge" style="border-color:'+occColor(c.occ)+';color:'+occColor(c.occ)+'">'+c.dong+'<br><b>'+c.occ+'%</b><span class=occ-n>·'+c.n+'</span></div>'})}).addTo(circles)
    })
    rentC.clearLayers()
    if(show.rent)(d.rent||[]).forEach(function(r){
      var m=L.marker([r.lat,r.lng],{icon:L.divIcon({className:'',iconSize:[16,16],
        html:'<div class=pin style="background:'+occColor(r.occ)+'"></div>'})})
      m.__d=r; m.on('click',function(){openList([r],'rent')})
      rentC.addLayer(m)
    })
    navC.clearLayers()
    if(show.naver)(d.naver||[]).forEach(function(n){
      var m=L.marker([n.lat,n.lng],{icon:L.divIcon({className:'',iconSize:[13,13],html:'<div class="pin nv"></div>'})})
      m.__d=n; m.on('click',function(){openList([n],'nv')})
      navC.addLayer(m)
    })
    document.getElementById('stat').textContent = z<MARKER_ZOOM
      ? '동별 예약률 '+(d.circles||[]).length+'개 — 확대하면 매물 표시'
      : '렌트 '+(d.rent||[]).length+' · 부동산 '+(d.naver||[]).length+' · 렌트 점 색=예약률'
  }).catch(function(){})
}
;['circles','rent','naver'].forEach(function(k){
  document.getElementById('t_'+k).addEventListener('click',function(){
    show[k]=!show[k]; this.classList.toggle('on',show[k]); load()
  })
})
load()
</script>
</body></html>"""
