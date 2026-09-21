import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

// En développement, l'API (et son WebSocket) est atteinte via le proxy : mêmes origines, donc
// les cookies de session HttpOnly SameSite=Strict fonctionnent sans configuration CORS.
// API_TARGET : API lancée à la main (défaut) ou pile Docker (`API_TARGET=https://localhost`,
// certificat auto-signé accepté).
export default defineConfig(({ mode }) => {
  const target = loadEnv(mode, '.', '').API_TARGET || 'http://127.0.0.1:8000'
  return {
    server: {
      proxy: {
        '/api': { target, ws: true, secure: false },
      },
    },
    build: {
      rollupOptions: {
        // La page de démonstration des widgets (avec données simulées) reste disponible sous /demo.html.
        input: { main: 'index.html', demo: 'demo.html' },
      },
    },
    test: {
      environment: 'jsdom',
      include: ['src/**/*.test.ts'],
    },
  }
})
