import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Standard Vite + React setup. Runs on 5174 (not 5173) so it can run
// alongside packages/front-end without a port clash.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5174,
  },
})
