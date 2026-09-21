import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import { ADMIN, apiLogin, createSynoptic, deleteSynoptic, ENGINEER, findPoint, makeDoc, uniqueName, uiLogin, widget } from './helpers'

/** La politique de sécurité de contenu réellement servie par Nginx : une seule source de vérité. */
function nginxCsp(): string {
  const conf = readFileSync(new URL('../../deploy/nginx/security-headers.conf', import.meta.url), 'utf8')
  const match = /Content-Security-Policy "([^"]+)"/.exec(conf)
  if (!match) throw new Error('CSP introuvable dans deploy/nginx/security-headers.conf')
  return match[1] as string
}

// 1×1 pixel PNG : exerce img-src data:.
const PIXEL =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

test('aucune violation de la CSP stricte sur les pages principales', async ({ page, request }) => {
  const csp = nginxCsp()
  // Impose la CSP de production à toutes les réponses, y compris lorsque le test vise une API sans Nginx.
  await page.route('**/*', async (route) => {
    const response = await route.fetch()
    await route.fulfill({ response, headers: { ...response.headers(), 'content-security-policy': csp } })
  })
  const violations: string[] = []
  page.on('console', (message) => {
    if (/content security policy|refused to (apply|load|execute|connect|frame)/i.test(message.text())) violations.push(message.text())
  })
  page.on('pageerror', (error) => violations.push(`pageerror: ${error.message}`))
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (event) => {
      console.error(`Content Security Policy: ${event.violatedDirective} ${event.blockedURI}`)
    })
  })

  await apiLogin(request, ENGINEER)
  const point = await findPoint(request, '-AI-')
  const doc = makeDoc(uniqueName('E2E csp'), [
    widget('v', 'value', 40, 40, 300, 160, { bind: { point: point.id } }),
    widget('g', 'gauge', 380, 40, 300, 220, { bind: { point: point.id } }),
    widget('t', 'trend', 40, 300, 700, 300, { bind: { points: [{ point: point.id }], range: '1h' } }),
    widget('a', 'alarm_list', 780, 300, 500, 300, { bind: {} }),
    widget('i', 'image', 720, 40, 200, 200, { src: PIXEL }),
  ])
  doc.canvas.bg_image = PIXEL
  const record = await createSynoptic(request, doc)
  try {
    await uiLogin(page, ADMIN)
    await page.goto(`/#/view/${record.slug}`)
    await expect(page.locator('.viewer [data-widget-id]')).toHaveCount(5)
    await expect(page.locator('[data-widget-id="t"] canvas, [data-widget-id="t"] .u-over').first()).toBeVisible()
    await page.goto(`/#/edit/${record.slug}`)
    await expect(page.locator('.ed-item')).toHaveCount(5)
    await page.getByRole('button', { name: 'Aperçu en direct' }).click()
    await expect(page.locator('.ed-preview .viewer')).toBeVisible()
    await page.goto('/#/admin')
    await expect(page.getByRole('heading', { name: "Journal d'audit" })).toBeVisible()
    await page.waitForTimeout(1500) // laisse passer les erreurs asynchrones éventuelles
  } finally {
    await deleteSynoptic(request, record.id)
  }
  expect(violations, violations.join('\n')).toEqual([])
})

test("un compte se verrouille après 5 échecs, l'administrateur le déverrouille", async ({ page, request, browser }) => {
  const login = `e2e-lock-${Date.now().toString(36)}`
  const password = 'mot-de-passe-verrou-123'
  await apiLogin(request, ADMIN)
  const created = await request.post('/api/v1/users', { data: { login, password, role: 'viewer' } })
  expect(created.status()).toBe(201)
  const userId = ((await created.json()) as { id: string }).id
  try {
    await page.goto('/')
    for (let attempt = 1; attempt <= 5; attempt++) {
      await page.getByLabel('Identifiant').fill(login)
      await page.getByLabel('Mot de passe').fill('mauvais-mot-de-passe')
      await page.getByRole('button', { name: 'Se connecter' }).click()
      await expect(page.locator('.login .error')).toContainText('incorrect')
    }
    // Le bon mot de passe est désormais refusé, avec un message qui explique pourquoi.
    await page.getByLabel('Identifiant').fill(login)
    await page.getByLabel('Mot de passe').fill(password)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await expect(page.locator('.login .error')).toContainText('Trop de tentatives')
    await expect(page.getByRole('heading', { name: 'Synoptiques' })).toBeHidden()

    // Un administrateur voit le verrou et le lève.
    const adminPage = await (await browser.newContext({ ignoreHTTPSErrors: true })).newPage()
    await uiLogin(adminPage, ADMIN)
    await adminPage.goto('/#/admin')
    const row = adminPage.locator(`tr[data-login="${login}"]`)
    await expect(row).toContainText('Verrouillé')
    await row.getByRole('button', { name: 'Déverrouiller' }).click()
    await expect(row).not.toContainText('Verrouillé')
    await adminPage.context().close()

    await page.getByLabel('Identifiant').fill(login)
    await page.getByLabel('Mot de passe').fill(password)
    await page.getByRole('button', { name: 'Se connecter' }).click()
    await expect(page.getByRole('heading', { name: 'Synoptiques' })).toBeVisible()
  } finally {
    await request.delete(`/api/v1/users/${userId}`)
  }
})

test("l'administration crée un compte, change son rôle, le désactive, et tout est audité", async ({ page, request }) => {
  const login = `e2e-user-${Date.now().toString(36)}`
  await uiLogin(page, ADMIN)
  await page.goto('/#/admin')
  const form = page.locator('form.admin__form')
  await form.getByLabel('Identifiant').fill(login)
  await form.getByLabel('Mot de passe').fill('un-mot-de-passe-long-1')
  await form.getByLabel('Rôle').selectOption('operator')
  await form.getByRole('button', { name: 'Créer le compte' }).click()
  const row = page.locator(`tr[data-login="${login}"]`)
  await expect(row).toBeVisible()

  await row.getByLabel(`Rôle de ${login}`).selectOption('engineer')
  await expect(page.locator('.admin__status')).toContainText('rôle engineer')
  await row.getByLabel(`${login} actif`).uncheck()
  await expect(page.locator(`tr[data-login="${login}"]`)).toContainText('Désactivé')

  // Le compte désactivé ne peut plus se connecter.
  const denied = await request.post('/api/v1/auth/login', { data: { login, password: 'un-mot-de-passe-long-1' } })
  expect(denied.status()).toBe(401)

  await page.getByRole('searchbox', { name: /Filtrer le journal/ }).fill('user.')
  await page.getByRole('button', { name: 'Actualiser' }).click()
  const audit = page.getByRole('table', { name: "Journal d'audit" })
  await expect(audit).toContainText('user.create')
  await expect(audit).toContainText('user.update')
  await expect(audit).toContainText(login)

  page.once('dialog', () => undefined)
  await row.getByRole('button', { name: 'Supprimer' }).click()
  await page.locator('.syn-confirm').getByRole('button', { name: 'Confirmer' }).click()
  await expect(page.locator(`tr[data-login="${login}"]`)).toHaveCount(0)
})

test("un ingénieur n'accède pas à l'administration", async ({ page }) => {
  await uiLogin(page, ENGINEER)
  await expect(page.getByRole('link', { name: 'Administration' })).toHaveCount(0)
  await page.goto('/#/admin')
  await expect(page.getByText('réservée aux administrateurs')).toBeVisible()
})
