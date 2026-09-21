import { defineConfig } from 'vitest/config'

// En développement, l'API (et son WebSocket) est atteinte via le proxy : mêmes origines, donc
// les cookies de session HttpOnly SameSite=Strict fonctionnent sans configuration CORS.
export default defineConfig({
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', ws: true },
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
})
