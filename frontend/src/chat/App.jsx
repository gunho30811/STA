import { useState, useEffect, useCallback } from 'react'
import { getJSON, sendJSON } from '../shared/api.js'
import './styles.css'
import AccountsPanel from './AccountsPanel.jsx'
import Inbox from './Inbox.jsx'

export default function App() {
  const [accounts, setAccounts] = useState([])
  const [rooms, setRooms] = useState([])
  const [refreshing, setRefreshing] = useState(false)

  // 채팅은 항상 신선해야 하므로 캐시 끔(ttl:0)
  const loadAccounts = useCallback(async () => {
    setAccounts(await getJSON('api/accounts', { ttl: 0 }))
  }, [])

  const loadRooms = useCallback(async () => {
    setRooms(await getJSON('api/rooms', { ttl: 0 }))
  }, [])

  // 계정/방이 바뀌면 둘 다 다시 로드
  const reloadAll = useCallback(() => {
    loadAccounts()
    loadRooms()
  }, [loadAccounts, loadRooms])

  useEffect(() => {
    loadAccounts()
    loadRooms()
  }, [loadAccounts, loadRooms])

  // "지금 새로고침": 연결 계정 즉시 폴링 → 방 목록 갱신
  async function refreshNow() {
    setRefreshing(true)
    try {
      await sendJSON('api/poll', 'POST')
      await loadRooms()
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <>
      <header>
        <div>
          <h1>💬 렌트 통합 채팅</h1>
          <p>연결한 여러 렌트 계정의 채팅을 한 곳에서 확인</p>
        </div>
        <button className="btn btn-ghost" disabled={refreshing} onClick={refreshNow}>
          {refreshing ? '새로고침 중...' : '지금 새로고침'}
        </button>
      </header>

      <div className="wrap">
        <AccountsPanel accounts={accounts} onChanged={reloadAll} />
        <Inbox rooms={rooms} onReloadRooms={loadRooms} />
      </div>
    </>
  )
}
