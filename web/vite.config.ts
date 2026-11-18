import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// During development the API runs separately: uvicorn api.main:app --port 8000
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: { chunkSizeWarningLimit: 900 },
})
