import { fmt as n, fmtComp } from '../shared/api.js'

// 오른쪽 상세 패널. 기존 showDetail() 이식. React라 innerHTML 없이 JSX로 렌더 → XSS 자동 차단.
function KV({ k, v }) {
  return (
    <div className="d-kv"><span className="k">{k}</span><span className="v">{v}</span></div>
  )
}

export default function Detail({ item, onClose }) {
  // 선택 전: 데스크탑은 안내 패널, 모바일은 숨김(.empty가 모바일에서 display:none).
  if (!item) {
    return (
      <div className="detail empty">
        <div className="d-empty">왼쪽 매물을 클릭하면<br />여기에 상세가 떠요</div>
      </div>
    )
  }
  const x = item
  return (
    <>
      {/* 모바일 바텀시트 뒤 어둡게(데스크탑은 CSS로 숨김) — 눌러서 닫기 */}
      <div className="sheet-overlay" onClick={onClose} />
      <div className="detail has-item">
        {/* 모바일 바텀시트 닫기 버튼(데스크탑은 숨김) */}
        <button className="sheet-close" onClick={onClose} aria-label="닫기">×</button>
      <div className="d-title">{x.name || ''}</div>
      <div className="d-sub">
        {x.btype || ''} · {x.rooms || ''} · {x.sigungu || ''} {x.dong || ''} · {x.pyeong || '-'}평
      </div>
      <div className="d-net">
        기대 월순수익 <b>{n(x.expNet)}</b> 만원{' '}
        <span style={{ fontSize: 11 }}>(예약률 {n(x.occ)}% 반영 = 실현매출−부동산월총)</span>
        <br />
        <span style={{ fontSize: 12, color: '#9ca3af' }}>
          풀가동 순수익 {n(x.net)}만 · 최대수익 {n(x.maxRev)}만 (예약률 100% 가정 상한)
        </span>
      </div>
      <div className="d-sec">
        <h4>렌트 단기임대</h4>
        <KV k="주당 임대료" v={`${n(x.wk)} 만원`} />
        <KV k="실현매출(월, 예약률 반영)" v={`${n(x.realRev)} 만원`} />
        <KV k="최대수익(월환산, 풀가동)" v={`${n(x.maxRev)} 만원`} />
        <KV k="1달 예약일 / 막힘" v={`${n(x.bk)} / ${n(x.bl)} 일`} />
        <KV k="예약률" v={`${n(x.occ)} %`} />
      </div>
      <div className="d-sec">
        <h4>부동산 장기월세 (매칭 기준)</h4>
        <KV k="보증금" v={`${n(x.nDep)} 만원`} />
        <KV k="월세" v={`${n(x.nRent)} 만원`} />
        <KV k="환산월세" v={`${n(x.nEquiv)} 만원`} />
        <KV k="관리비" v={`${n(x.nMgmt)} 만원 (${x.mgmtFlag || ''})`} />
        <KV k="월총(환산+관리비)" v={`${n(x.nTotal)} 만원`} />
        <KV k="배수(월총÷주당)" v={n(x.mult)} />
        <KV k="매칭 매물수" v={n(x.matches)} />
      </div>
      <div className="d-sec">
        <h4>건물 · 지역</h4>
        <KV k="이 건물 렌트 매물수" v={n(x.samBldg)} />
        <KV k="동 경쟁(렌트)" v={fmtComp(x.dongCnt)} />
        <KV k="동 예약률" v={`${n(x.dongOcc)} %`} />
        <KV k="인근역" v={`${x.station || '-'} (역예약률 ${n(x.stOcc)}%)`} />
        <KV k="건물 부동산 매물수" v={n(x.bldgCnt)} />
        <KV k="건물 월세(최저/중간/최고)" v={`${n(x.bldgRentMin)}/${n(x.bldgRentMed)}/${n(x.bldgRentMax)}`} />
      </div>
      <div>
        {x.naverUrl && <a className="lnk n" href={x.naverUrl} target="_blank" rel="noreferrer">부동산</a>}
        {x.samUrl && <a className="lnk s" href={x.samUrl} target="_blank" rel="noreferrer">렌트</a>}
      </div>
      </div>
    </>
  )
}
