import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // index.html (the terminal) reads live data from the Python journal server:
    //   python dashboard/server.py   (or: make dashboard)
    proxy: { '/api': 'http://127.0.0.1:8050' },
  },
})
