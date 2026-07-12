import { useState } from 'react'
import { sendJSON } from '../shared/api.js'
import { statusLabel } from './helpers.js'

// 연결된 삼삼 계정 목록 + 새 계정 연결 폼.
// props:
//   accounts   - 계정 배열
//   onChanged  - 계정이 추가/삭제됐을 때(목록·방 재로딩) 호출
export default function AccountsPanel({ accounts, onChanged }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [label, setLabel] = useState('')
  const [msg, setMsg] = useState({ text: '', ok: false })
  const [adding, setAdding] = useState(false)

  async function addAccount() {
    if (!email.trim() || !password) {
      setMsg({ text: '이메일/비밀번호를 입력해주세요.', ok: false })
      return
    }
    setMsg({ text: '로그인 확인 중... (최대 20초)', ok: false })
    setAdding(true)
    try {
      const { ok, data } = await sendJSON('api/accounts', 'POST', {
        email: email.trim(),
        password,
        label: label.trim(),
      })
      if (!ok) {
        setMsg({ text: (data && data.error) || '연결 실패', ok: false })
        return
      }
      const done = data && data.pending
        ? data.message
        : '연결되었습니다. 새로고침 버튼으로 채팅을 불러오세요.'
      setMsg({ text: done, ok: true })
      setEmail('')
      setPassword('')
      setLabel('')
      onChanged()
    } finally {
      setAdding(false)
    }
  }

  async function deleteAccount(id) {
    if (!confirm('이 계정 연결을 해제할까요?')) return
    await sendJSON(`api/accounts/${id}`, 'DELETE')
    onChanged()
  }

  return (
    <div className="panel">
      <h2 className="sec">연결된 삼삼 계정</h2>

      <div className="accounts">
        {accounts.length === 0 && (
          <span style={{ color: '#9ca3af', fontSize: 12.5 }}>
            연결된 계정이 없습니다. 아래에서 추가해주세요.
          </span>
        )}

        {accounts.map((a) => (
          <div className="acct" key={a.id}>
            <button className="del" title="연결 해제" onClick={() => deleteAccount(a.id)}>✕</button>
            <div className="label">{a.label || a.samsam_email}</div>
            <div className="email">{a.samsam_email}</div>
            <span className={`status ${a.status}`}>{statusLabel(a.status)}</span>
          </div>
        ))}
      </div>

      <div className="addform">
        <div className="fg">
          <label>삼삼 이메일</label>
          <input
            type="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="fg">
          <label>비밀번호</label>
          <input
            type="password"
            placeholder="비밀번호"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <div className="fg">
          <label>별칭(선택)</label>
          <input
            type="text"
            placeholder="예: 강남계정"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </div>
        <button className="btn btn-go" disabled={adding} onClick={addAccount}>계정 연결</button>
      </div>

      {msg.text && <div className={msg.ok ? 'msg ok' : 'msg'}>{msg.text}</div>}
    </div>
  )
}
