import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // Allow Cloudflare Quick Tunnel hostnames, which use random ephemeral
    // subdomains of trycloudflare.com. Without this Vite blocks requests whose
    // Host header is not localhost/127.0.0.1 (403 "Blocked request").
    // The ".trycloudflare.com" suffix entry matches every Quick Tunnel domain,
    // so no ephemeral hostname has to be hard-coded here.
    allowedHosts: [
      'localhost',
      '127.0.0.1',
      '0.0.0.0',
      '.trycloudflare.com',
    ],
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/uploads': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/auth': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
  },
  test: {
    exclude: ['**/e2e/**', '**/node_modules/**', '**/dist/**', '**/.{idea,git,cache,output,temp}/**'],
  },
});