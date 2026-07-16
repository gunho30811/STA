# -*- coding: utf-8 -*-
"""전용 풀스크린 지도(/map) 페이지 템플릿.

성능 설계 v3 (줌 덜컥거림·배지 깜빡임 피드백 반영):
- 렌트 클러스터링을 markercluster → **supercluster**(mapbox 엔진)로 교체:
  22k 포인트 인덱스 1회(~150ms) 후 뷰포트마다 getClusters() ~수 ms → 줌/팬 즉각.
- 렌더는 **diff 방식**: 화면에 이미 있는 클러스터/핀/동배지는 건드리지 않고
  새로 보이는 것만 추가·사라진 것만 제거 → 역삼동 배지가 깜빡이던 문제 해결.
- 데이터는 /samsam/api/map_all 최초 1회. 부동산(네이버)만 줌 15+에서 bbox 조회(~0.3s).
- 클러스터 클릭: ≤60개면 매물 리스트 모달(각 예약률), 초과면 해당 줌으로 확대.
"""

MAP_PAGE = """<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>rendit · 지도</title>
<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard-dynamic-subset.css">
<link rel=stylesheet href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
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
.occ-badge-wrap{background:none;border:none}
.occ-badge{transform:translate(-50%,-50%);background:rgba(255,255,255,.93);border:1.5px solid;border-radius:10px;
padding:3px 8px;font-size:11px;font-weight:700;text-align:center;line-height:1.25;white-space:nowrap;
box-shadow:0 1px 4px rgba(0,0,0,.2)}
.occ-badge b{font-size:13px}.occ-n{font-weight:500;color:#94a3b8;font-size:10px}
.pin{width:16px;height:16px;border-radius:50%;border:2.5px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35)}
.pin.nv{width:13px;height:13px;background:#14b8a6}
.clus{display:flex;align-items:center;justify-content:center;border-radius:50%;color:#fff;font-weight:800;
border:3px solid rgba(255,255,255,.85);box-shadow:0 2px 8px rgba(0,0,0,.35);line-height:1.1;text-align:center;cursor:pointer}
.clus.rent{background:#4321F3}.clus.nv{background:#0d9488}
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
</style></head><body>
<div id=map></div>
<div class=bar>
  <label class="tog on" id=t_circles><span class=dot style="background:#059669"></span>동별 예약률</label>
  <label class="tog on" id=t_rent><span class=dot style="background:#4321F3"></span>렌트</label>
  <label class="tog on" id=t_naver><span class=dot style="background:#14b8a6"></span>부동산</label>
  <span class=stat id=stat>렌트 매물 불러오는 중…</span>
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
function occColor(o){return o==null?'#94a3b8':o>=60?'#059669':o>=30?'#f59e0b':'#dc2626'}
function occCls(o){return o==null?'mut':o>=60?'good':o>=30?'midc':'bad'}

var map=L.map('map',{center:[37.5665,126.978],zoom:13,zoomAnimation:true})
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap'}).addTo(map)

var rentLayer=L.layerGroup().addTo(map)
var circleLayer=L.layerGroup().addTo(map)
var navLayer=L.layerGroup().addTo(map)
var show={circles:true,rent:true,naver:true}
var idx=null           // supercluster 인덱스(렌트 22k)
var CIRCLES=[]         // 전 동 [lat,lng,occ,n,dong]
var rentTotal=0

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

// ── diff 렌더 공통: key→layer 맵 유지, 새 것만 추가·없어진 것만 제거(깜빡임 방지) ──
function diffRender(group, shownMap, wanted){   // wanted: Map(key → makeLayerFn)
  shownMap.forEach(function(ly,key){ if(!wanted.has(key)){group.removeLayer(ly);shownMap.delete(key)} })
  wanted.forEach(function(make,key){
    if(shownMap.has(key)) return
    var ly=make(); shownMap.set(key,ly); group.addLayer(ly)
  })
}
var shownRent=new Map(), shownCirc=new Map()

// ── 렌트: supercluster — 뷰포트에 보이는 클러스터/핀만 diff 렌더 ──
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
            if(n>LIST_MAX){
              var ez=Math.min(idx.getClusterExpansionZoom(cid),18)
              map.setView([lat,lng],ez)
            }else{
              openList(idx.getLeaves(cid,Infinity).map(function(l){return l.properties}),'rent')
            }
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

// ── 동별 예약률 원: diff 렌더(이미 보이는 배지는 그대로 → 깜빡임 없음) ──
function renderCircles(){
  var wanted=new Map()
  if(show.circles && map.getZoom()>=CIRCLE_ZOOM){
    var b=map.getBounds().pad(0.1)
    CIRCLES.forEach(function(c){
      if(!b.contains([c[0],c[1]])) return
      wanted.set(c[4]+'_'+c[0], function(){
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

// ── 부동산(네이버): 줌 15+ bbox 조회 ──
var navTimer=null, navSeq=0
function loadNaver(){
  if(!show.naver||map.getZoom()<NAVER_ZOOM){navLayer.clearLayers();setStat();return}
  var b=map.getBounds(), seq=++navSeq
  var p=new URLSearchParams({min_lat:b.getSouth(),max_lat:b.getNorth(),min_lng:b.getWest(),max_lng:b.getEast(),rent:'0'})
  fetch('/samsam/api/map?'+p.toString(),{credentials:'same-origin'})
    .then(function(r){return r.json()}).then(function(d){
      if(seq!==navSeq) return
      navLayer.clearLayers()
      ;(d.naver||[]).forEach(function(n){
        var m=L.marker([n.lat,n.lng],{icon:L.divIcon({className:'',iconSize:[13,13],html:'<div class="pin nv"></div>'})})
        m.on('click',function(){openList([n],'nv')})
        navLayer.addLayer(m)
      })
      setStat((d.naver||[]).length)
    }).catch(function(){setStat()})
}
function setStat(navN){
  var z=map.getZoom(), s
  if(z<NAVER_ZOOM) s='렌트 '+rentTotal.toLocaleString()+'개 · 확대하면 부동산 표시 · 점 색=예약률'
  else s='부동산 '+(navN!=null?navN:0)+'개 · 점 색=예약률'
  document.getElementById('stat').textContent=s
}

// ── 초기 1회 로드 → supercluster 인덱스 ──
fetch('/samsam/api/map_all',{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(d){
  CIRCLES=d.circles||[]
  var feats=(d.rent||[]).map(function(a){
    return {type:'Feature',geometry:{type:'Point',coordinates:[a[2],a[1]]},
      properties:{id:a[0],lat:a[1],lng:a[2],occ:a[3],week:a[4],pyeong:a[5],btype:a[6],name:a[7],sigungu:a[8],dong:a[9]}}
  })
  rentTotal=feats.length
  idx=new Supercluster({radius:56,maxZoom:17,minPoints:2}).load(feats)
  renderRent(); renderCircles(); setStat()
}).catch(function(){document.getElementById('stat').textContent='로드 실패 — 새로고침 해주세요'})

// 이동 중에도 부드럽게(throttle), 끝나면 확정 렌더. 네이버만 디바운스 fetch.
var mvTimer=null
map.on('move',function(){
  if(mvTimer) return
  mvTimer=setTimeout(function(){mvTimer=null;renderRent();renderCircles()},120)
})
map.on('moveend zoomend',function(){
  renderRent(); renderCircles()
  clearTimeout(navTimer); navTimer=setTimeout(loadNaver,400)
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
