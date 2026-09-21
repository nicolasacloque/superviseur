import { defineConfig } from 'vitest/config'

// En développement, l'API (et son WebSocket) est atteinte via le proxy : mêmes origines, donc
// les cookies de session HttpOnly SameSite=Strict fonctionnent sans configuration CORS.
export default defineConfig({
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
  },
})
