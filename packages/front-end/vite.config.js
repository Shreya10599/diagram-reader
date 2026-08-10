import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Standard Vite + React setup. Nothing fancy needed here.
export default defineConfig({
  plugins: [react()],
  server: {
    // Camera access requires HTTPS in most browsers except on localhost,
    // so localhost dev is fine — just don't be surprised if it breaks
    // when you deploy and test on a phone over plain http.
    host: true,
  },
})
