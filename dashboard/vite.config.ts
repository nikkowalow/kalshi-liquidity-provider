import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Live data comes from the running bot's API (klp run serves it on :8050),
    // including the /api/ws WebSocket.
    proxy: { '/api': { target: 'http://127.0.0.1:8050', ws: true } },
  },
})
