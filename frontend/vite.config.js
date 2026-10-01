import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/',
  plugins: [react()],
  build: {
    outDir: 'dist',
    target: 'es2020',
    cssCodeSplit: true,
    sourcemap: false,
    assetsInlineLimit: 4096,
    chunkSizeWarningLimit: 600,
    reportCompressedSize: false,
    cssMinify: true,
    rollupOptions: {
      output: {
        // Split slow-moving vendor code out of the app bundle so repeat
        // visits reuse cached chunks and route-level lazy() boundaries
        // stay small.
        manualChunks: {
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],
          'vendor-query': ['@tanstack/react-query', 'axios', 'zod'],
          'vendor-charts': ['lightweight-charts'],
        },
      },
    },
  },
  server: {
    // Local-dev only: `vite dev` proxies /api to FastAPI.
    // Production builds ignore `server` entirely.
    // Point at a remote/local backend without editing code:
    //   VITE_PROXY_TARGET=http://192.168.1.20:8000 npm run dev
    // (BACKEND_URL is accepted as an alias; default stays localhost:8000.)
    port: 5173,
    proxy: {
      // Object form (not string shorthand) so the Host header is rewritten
      // for FastAPI and connection failures surface fast instead of hanging.
      '/api': {
        target: process.env.VITE_PROXY_TARGET || process.env.BACKEND_URL || 'http://localhost:8000',
        changeOrigin: true,
        timeout: 10000,
      },
      // Same routing as deploy/Caddyfile; without it the header shows OFFLINE in dev.
      '/health': {
        target: process.env.VITE_PROXY_TARGET || process.env.BACKEND_URL || 'http://localhost:8000',
        changeOrigin: true,
        timeout: 10000,
      },
    },
  },
  preview: {
    // Pinned so `npm run preview` serves on the same port as dev.
    port: 5173,
  },
});
