// 네이버 매물 근처(같은 동)·같은 유형·같은 평수(동일조건) 렌트 시세 수요·순수익 박스.
// 백엔드(_area_of)가 유형·정수평수 완전 동일 비교군만 내려줌 — 없으면 sa 자체가 null.
const occColor = (v) => (v >= 60 ? '#059669' : v < 30 ? '#dc2626' : '#0369a1')

function Line({ o, label, emoji }) {
  if (!o) return null
  return (
    <div style={{ marginTop: 3 }}>
      {emoji} {label}{' '}
      <a href={o.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
        style={{ color: '#0369a1', fontWeight: 700, textDecoration: 'underline' }}>{o.name}</a>{' '}
      예약률 <b style={{ color: occColor(o.occ) }}>{o.occ}%</b>{' '}
      <span style={{ color: '#64748b' }}>· 주당 {o.week}만</span>
    </div>
  )
}

export default function SamArea({ sa }) {
  if (!sa) return null
  return (
    <div style={{ marginTop: 8, padding: '7px 10px', background: '#eff6ff', border: '1px solid #dbeafe', borderRadius: 8, fontSize: 12, color: '#1e3a5f', lineHeight: 1.5 }}>
      <div>
        🛋️ 근처 동일조건(<b>{sa.pyeong}평 {sa.btype}</b>) 렌트 <b>{sa.n}실</b>
        {sa.avg_week != null && <> · 주당 평균 <b style={{ color: '#0369a1' }}>{sa.avg_week}만</b></>}
      </div>
      {sa.n >= 2 ? (
        <><Line o={sa.best} label="잘나감" emoji="👑" /><Line o={sa.worst} label="안나감" emoji="🥶" /></>
      ) : (
        <Line o={sa.best} label="예시" emoji="🛋️" />
      )}
      {sa.net != null && (
        <div style={{ marginTop: 5, paddingTop: 5, borderTop: '1px dashed #bfdbfe', fontSize: 12.5 }}>
          💰 렌트 운영 시 예상 <b>월순수익</b>{' '}
          <b style={{ color: sa.net >= 0 ? '#059669' : '#dc2626', fontSize: 14 }}>
            {sa.net >= 0 ? '+' : ''}{sa.net.toLocaleString()}만
          </b>{' '}
          <span style={{ color: '#94a3b8', cursor: 'help' }}
            title={`잘나감 렌트 월매출 ${sa.sam_rev}만 − 이 매물 월세 − 관리비 ${sa.mgmt}만`}>ⓘ</span>
        </div>
      )}
    </div>
  )
}
