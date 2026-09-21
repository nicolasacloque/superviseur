import { defineConfig } from '@playwright/test'

// Variables : E2E_BASE_URL (défaut http://127.0.0.1:8000), E2E_CHANNEL (ex. « chrome » pour utiliser
// le Chrome installé plutôt que le Chromium de Playwright), identifiants dans tests/helpers.ts.
const ci = Boolean(process.env.CI)

export default defineConfig({
  testDir: 'tests',
  outputDir: 'test-results',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  // Les scénarios partagent un simulateur et un serveur : exécution en série.
  workers: 1,
  fullyParallel: false,
  retries: ci ? 1 : 0,
  reporter: ci ? [['github'], ['list']] : [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8000',
    channel: process.env.E2E_CHANNEL || undefined,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    locale: 'fr-FR',
  },
})
