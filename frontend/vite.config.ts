import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/',
  plugins: [react()],
  build: {
    outDir: 'dist',
  },
  server: {
    // Local-dev only: `vite dev` proxies /api to FastAPI.
    // Production/Vercel builds ignore `server` entirely.
    port: 5173,
    proxy: {
      // Object form (not string shorthand) so the Host header is rewritten
      // for FastAPI and connection failures surface fast instead of hanging.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        timeout: 10000,
      },
    },
  },
  preview: {
    // Pinned so `vite preview` (Docker HEALTHCHECK + local `npm run preview`)
    // always serves the same port the compose file probes.
    port: 5173,
  },
});
