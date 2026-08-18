// 통합 채팅 공용 헬퍼.

// 계정 상태 코드 → 한글 배지 문구
export function statusLabel(status) {
  if (status === 'ok') return '정상'
  if (status === 'pending_login') return '연결 처리 중'
  if (status === 'pending_provider') return '연동 준비 중'
  if (status === 'reauth_needed') return '재연결 필요'
  return '오류'
}

// epoch(ms) → "6/24 14:30" 같은 한국식 짧은 시각
export function fmtTime(ms) {
  if (!ms) return ''
  const d = new Date(Number(ms))
  return d.toLocaleString('ko-KR', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// 방 표시 이름(상대 닉네임 우선)
export function roomTitle(room) {
  return room.counterpart_nickname || room.room_name || '(이름없음)'
}
