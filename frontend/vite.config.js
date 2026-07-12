import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 이 뷰어는 통합 포털의 /profit/ 아래에 마운트된다(DispatcherMiddleware).
// 그래서 base='/profit/'로 두면 빌드 산출물의 에셋 경로와 API 상대경로가 프로덕션과 일치한다.
//
// 개발(vite dev, :5173): /profit/api/* 와 /auth/* 를 Flask(:8000)로 프록시해
// 브라우저 입장에선 같은 오리진이 되게 한다 → 세션 쿠키가 그대로 실려 로그인이 dev에서도 동작.
export default defineConfig({
  base: '/profit/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/profit/api': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
    },
  },
  build: {
    // Flask/Vercel이 서빙할 정적 산출물. web/profit_app.py 가 이 폴더를 읽는다.
    outDir: 'dist',
    emptyOutDir: true,
  },
})
