// 이 동네 아파트 매매 시세(국토부 실거래) — 주변 소비력을 가늠하려고 붙인다.
// compact: 카드용 한 줄 / 아니면 모달용 상세(구간·대표단지·최근 거래).

// 만원 단위 금액 → '6.8억' / '9,000만'
export function eok(man) {
  if (man == null) return '-'
  if (man >= 10000) return `${(man / 10000).toFixed(man >= 100000 ? 0 : 1)}억`
  return `${Math.round(man).toLocaleString()}만`
}

export default function AptPrice({ ap, compact = false, trades = [] }) {
  if (!ap || !ap.median_per_pyeong) return null
  const where = ap.scope === '시군구' ? `${ap.sigungu} 전체` : `${ap.dong}`

  if (compact) {
    return (
      <div className="aptp" title={`${where} 아파트 매매 실거래 ${ap.n}건(최근 ${ap.months}개월) 기준`}>
        🏢 {where} 아파트 <b>평당 {ap.median_per_pyeong.toLocaleString()}만</b>
        <span className="mut"> · 중앙 {eok(ap.median_amount)}</span>
      </div>
    )
  }

  return (
    <div className="sec">
      <h3>주변 아파트 매매 시세 (소비력)</h3>
      <div className="aptbox">
        <div className="aptrow">
          <div>
            <div className="aptk">{where} 평당가(중앙)</div>
            <div className="aptv">{ap.median_per_pyeong.toLocaleString()}<small>만원/평</small></div>
          </div>
          <div>
            <div className="aptk">거래가(중앙)</div>
            <div className="aptv">{eok(ap.median_amount)}</div>
          </div>
          <div>
            <div className="aptk">평당가 구간(25~75%)</div>
            <div className="aptv sm">{ap.p25_per_pyeong?.toLocaleString()} ~ {ap.p75_per_pyeong?.toLocaleString()}<small>만</small></div>
          </div>
        </div>
        <div className="aptmeta">
          최근 {ap.months}개월 실거래 {ap.n?.toLocaleString()}건
          {ap.avg_build_year ? ` · 평균 준공 ${ap.avg_build_year}년` : ''}
          {ap.top_apt ? ` · 대표단지 ${ap.top_apt}(평당 ${ap.top_apt_per_pyeong?.toLocaleString()}만)` : ''}
          {ap.scope === '시군구' && ' · 동 표본이 적어 시군구 기준'}
        </div>
        {trades.length > 0 && (
          <div className="apttrades">
            {trades.map((t, i) => (
              <div className="apttrade" key={i}>
                <span className="tn">{t.apt_name}</span>
                <span className="tm">{Math.round(t.area_m2 / 3.305785)}평 {t.floor != null ? `${t.floor}층` : ''}</span>
                <span className="tp">{eok(t.amount)}</span>
                <span className="td">{(t.deal_date || '').slice(2, 10)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
