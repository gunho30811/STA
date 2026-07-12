import { useState, useEffect, useRef, useCallback } from 'react'
import { getJSON, sendJSON } from '../shared/api.js'
import { fmtTime, roomTitle } from './helpers.js'

// 통합 채팅함: 왼쪽 방 목록 + 오른쪽 스레드/전송.
// props:
//   rooms          - 방 배열(App이 로딩)
//   onReloadRooms  - 방 목록 다시 불러오기(읽음 처리·새 메시지 반영)
export default function Inbox({ rooms, onReloadRooms }) {
  const [curRoom, setCurRoom] = useState(null)
  const [thread, setThread] = useState(null)   // { owner_id, messages, pending } | null
  const [sendText, setSendText] = useState('')
  const [sending, setSending] = useState(false)
  const threadRef = useRef(null)

  const roomsById = Object.fromEntries(rooms.map((r) => [r.id, r]))

  // 방 열기: 메시지 로드 → 서버가 읽음 처리하므로 방 목록도 갱신
  const openRoom = useCallback(async (id) => {
    setCurRoom(id)
    const res = await getJSON(`api/rooms/${id}/messages`, { ttl: 0 })
    if (res.error) {
      setThread(null)
      return
    }
    setThread(res)
    onReloadRooms()
  }, [onReloadRooms])

  // 스레드가 바뀌면 맨 아래로 스크롤
  useEffect(() => {
    const el = threadRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [thread])

  // 20초마다 가볍게 갱신: 방이 열려 있으면 그 방을, 아니면 목록만.
  useEffect(() => {
    const timer = setInterval(() => {
      if (curRoom) openRoom(curRoom)
      else onReloadRooms()
    }, 20000)
    return () => clearInterval(timer)
  }, [curRoom, openRoom, onReloadRooms])

  async function send() {
    const message = sendText.trim()
    if (!curRoom || !message) return
    setSending(true)
    try {
      const { ok, data } = await sendJSON(`api/rooms/${curRoom}/send`, 'POST', { message })
      if (ok) {
        setSendText('')
        openRoom(curRoom)
      } else {
        alert((data && data.error) || '전송 실패')
      }
    } finally {
      setSending(false)
    }
  }

  function onSendKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const unread = rooms.filter((r) => r.unread)
  const rest = rooms.filter((r) => !r.unread)
  const curInfo = curRoom ? roomsById[curRoom] : null

  return (
    <div className="panel">
      <h2 className="sec">통합 채팅함</h2>

      <div className="md">
        <div className="roomlist">
          {rooms.length === 0 && <div className="empty">아직 채팅방이 없습니다</div>}

          {unread.length > 0 && (
            <>
              <div className="roomgrp">미확인 ({unread.length})</div>
              {unread.map((r) => (
                <RoomRow key={r.id} room={r} active={r.id === curRoom} onOpen={() => openRoom(r.id)} />
              ))}
            </>
          )}

          {rest.length > 0 && (
            <>
              <div className="roomgrp">전체</div>
              {rest.map((r) => (
                <RoomRow key={r.id} room={r} active={r.id === curRoom} onOpen={() => openRoom(r.id)} />
              ))}
            </>
          )}
        </div>

        <div className="threadwrap">
          {curInfo && (
            <div className="threadhdr">
              {roomTitle(curInfo)}
              {curInfo.room_name && curInfo.counterpart_nickname && (
                <span className="sub">{curInfo.room_name}</span>
              )}
            </div>
          )}

          <div className="thread" ref={threadRef}>
            {!thread && <div className="empty">왼쪽에서 채팅방을 선택하세요</div>}

            {thread && thread.messages.map((m, i) => {
              const mine = String(m.sender) === String(thread.owner_id)
              const kind = m.message_type === 'system' ? 'system' : mine ? 'me' : 'other'
              return (
                <div className={`bubble ${kind}`} key={m.msg_key || i}>
                  {m.message}
                  <span className="t">{fmtTime(m.message_time)}</span>
                </div>
              )
            })}

            {thread && (thread.pending || []).map((p) => (
              <div className="bubble me pending" key={`p${p.id}`}>
                {p.message}
                <span className="t">전송 대기 중...</span>
              </div>
            ))}
          </div>

          {curRoom && (
            <div className="sendbar">
              <textarea
                placeholder="답장을 입력해 주세요"
                value={sendText}
                onChange={(e) => setSendText(e.target.value)}
                onKeyDown={onSendKeyDown}
              />
              <button className="btn btn-go" disabled={sending} onClick={send}>전송</button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// 방 목록의 한 줄
function RoomRow({ room, active, onOpen }) {
  const last = (room.last_message || '').replace(/\n/g, ' ')
  return (
    <div className={active ? 'room on' : 'room'} onClick={onOpen}>
      <div className="rn">
        <span>
          {room.unread && <span className="dot" />}
          {roomTitle(room)}
        </span>
        <span className="lbl">{room.label || room.samsam_email}</span>
      </div>
      <div className="last">{last}</div>
      <div className="time">{fmtTime(room.last_message_time)}</div>
    </div>
  )
}
