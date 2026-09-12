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
      '/api': 'http://localhost:8000',
    },
  },
});
