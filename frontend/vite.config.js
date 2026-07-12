import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 뷰어별로 base·outDir 을 다르게 빌드한다(각 Flask 앱이 /profit/, /gangnam/ ... 로 마운트됨).
//   VIEWER=gangnam npm run build  →  base '/gangnam/', dist/gangnam
// default 는 profit.
const viewer = process.env.VIEWER || 'profit'
// 대부분 /{viewer}/ 지만, 채팅은 삼삼 앱 하위(/samsam/chat/)에 마운트된다.
const BASES = { chat: '/samsam/chat/' }
const base = BASES[viewer] || `/${viewer}/`

export default defineConfig({
  base,
  define: { 'import.meta.env.VITE_VIEWER': JSON.stringify(viewer) },
  plugins: [react()],
  server: {
    port: 5173,
    // dev(:5173)에서 API·로그인을 Flask(:8000)로 프록시 → 같은 오리진이라 세션쿠키 동작.
    proxy: {
      '/profit/api': 'http://localhost:8000',
      '/gangnam/api': 'http://localhost:8000',
      '/samsam/chat/api': 'http://localhost:8000',
      '/samsam/api': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
    },
  },
  build: {
    outDir: `dist/${viewer}`,
    emptyOutDir: true,
  },
})
