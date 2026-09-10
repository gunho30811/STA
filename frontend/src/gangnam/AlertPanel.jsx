import { useState, useEffect } from 'react'
import { getJSON, sendJSON } from '../shared/api.js'

// 조건 알림 패널 — 지금 화면의 검색조건을 저장해두면, 그 조건에 새 매물이 들어올 때
// 카카오톡('나에게 보내기')으로 알려준다. 저장·조회·삭제는 /gangnam/api/alerts.
export default function AlertPanel({ currentQuery, onClose }) {
  const [data, setData] = useState(null)
  const [name, setName] = useState('')
  const [interval, setInterval_] = useState(6)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = () => getJSON('api/alerts', { ttl: 0 }).then(setData).catch(() => setData({ items: [], kakao_ready: false }))
  useEffect(() => { load() }, [])

  const add = async () => {
    setBusy(true); setMsg('')
    const r = await sendJSON('api/alerts', 'POST', { name: name.trim(), query: currentQuery, interval })
    setBusy(false)
    if (!r.ok) { setMsg(r.data?.error || '저장하지 못했습니다.'); return }
    setName(''); setData((d) => ({ ...d, items: r.data.items }))
    setMsg(`저장했습니다. ${interval}시간마다 이 조건만 따로 크롤해서 새 매물이 있으면 카톡으로 보내드려요.`)
  }
  const patch = async (id, body) => {
    const r = await sendJSON(`api/alerts/${id}`, 'PATCH', body)
    if (r.ok) setData((d) => ({ ...d, items: r.data.items }))
  }
  const remove = async (id) => {
    const r = await sendJSON(`api/alerts/${id}`, 'DELETE')
    if (r.ok) setData((d) => ({ ...d, items: r.data.items }))
  }
  const test = async (id) => {
    setBusy(true); setMsg('')
    const r = await sendJSON(`api/alerts/${id}/test`, 'POST')
    setBusy(false)
    if (!r.ok) { setMsg('발송 테스트에 실패했습니다.'); return }
    setMsg(r.data.sent
      ? `카톡을 보냈습니다 (최근 24시간 새 매물 ${r.data.new}건).`
      : (r.data.new ? '카톡 연결이 안 돼 발송하지 못했습니다.' : '최근 24시간 안에 들어온 새 매물이 없습니다.'))
  }

  const items = data?.items || []
  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal alertmodal" onClick={(e) => e.stopPropagation()}>
        <div className="mhead">
          <div>
            <h2>🔔 조건 알림</h2>
            <div className="ma">지금 검색조건에 맞는 새 매물이 등록되면 카카오톡으로 보내드려요.</div>
          </div>
          <button className="x" onClick={onClose}>×</button>
        </div>
        <div className="mbody">
          {data && !data.kakao_ready && (
            <div className="alertwarn">
              카카오톡 알림이 아직 연결되지 않았습니다.
              <a href="/auth/kakao?notify=1"> 카톡 알림 연결하기 →</a>
            </div>
          )}

          <div className="alertadd">
            <input value={name} maxLength={40} placeholder="알림 이름 (예: 강남 오피스텔 월 90 이하)"
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !busy) add() }} />
            <select value={interval} onChange={(e) => setInterval_(Number(e.target.value))} title="이 주기마다 이 조건만 따로 크롤해서 확인합니다">
              {(data?.intervals || [1, 2, 4, 6, 12]).map((h) => (
                <option key={h} value={h}>{h}시간마다</option>
              ))}
            </select>
            <button className="btn btn-go" disabled={busy} onClick={add}>이 조건 저장</button>
          </div>
          <div className="alertq">현재 조건: {decodeURIComponent(currentQuery.replace(/&?(page|size)=[^&]*/g, '')) || '(전체 매물)'}</div>
          {msg && <div className="alertmsg">{msg}</div>}

          <div className="sec" style={{ marginTop: 18 }}>
            <h3>내 알림 {items.length > 0 && `(${items.length}/${data.max})`}</h3>
            {items.length === 0
              ? <div className="empty" style={{ padding: '24px 0' }}>저장한 알림이 없습니다.</div>
              : items.map((a) => (
                <div className="alertrow" key={a.id}>
                  <div className="ai">
                    <div className="an">{a.name}</div>
                    <div className="ad">
                      {a.last_crawl_at ? `최근 크롤 ${a.last_crawl_at.slice(5, 16)} · ` : ''}
                      {a.last_sent_at ? `최근 발송 ${a.last_sent_at.slice(5, 16)}` : '아직 발송 없음'}
                      {a.sent_count > 0 && ` · 누적 ${a.sent_count}건`}
                      {' · '}<a href={a.link}>조건 열기</a>
                    </div>
                  </div>
                  <div className="ac">
                    <select className="aint" value={a.interval_hours || 6}
                      onChange={(e) => patch(a.id, { interval: Number(e.target.value) })}>
                      {(data?.intervals || [1, 2, 4, 6, 12]).map((h) => (
                        <option key={h} value={h}>{h}시간마다</option>
                      ))}
                    </select>
                    <label className="atog">
                      <input type="checkbox" checked={!!a.enabled} onChange={(e) => patch(a.id, { enabled: e.target.checked })} />
                      {a.enabled ? '켜짐' : '꺼짐'}
                    </label>
                    <button className="btn btn-reset" disabled={busy} onClick={() => test(a.id)}>테스트</button>
                    <button className="btn btn-del" onClick={() => remove(a.id)}>삭제</button>
                  </div>
                </div>
              ))}
          </div>
        </div>
      </div>
    </div>
  )
}
