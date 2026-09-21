import { expect, test } from '@playwright/test'
import {
  apiLogin,
  createSynoptic,
  deleteSynoptic,
  ENGINEER,
  findPoint,
  latestValue,
  makeDoc,
  OPERATOR,
  type Point,
  type SavedSynoptic,
  uiLogin,
  uniqueName,
  widget,
} from './helpers'

const created: string[] = []

test.afterEach(async ({ request }) => {
  await apiLogin(request, ENGINEER).catch(() => undefined)
  for (const id of created.splice(0)) await deleteSynoptic(request, id)
})

/** Widgets à créer par l'éditeur : type, fragment du nom de point à lier. */
const TEN_WIDGETS: [string, string][] = [
  ['value', '-AI-'],
  ['value', '-AV-'],
  ['gauge', '-AI-'],
  ['gauge', '-AV-'],
  ['indicator', '-BI-'],
  ['indicator', '-BV-'],
  ['switch', '-BV-'],
  ['setpoint', '-AV-'],
  ['trend', '-AI-'],
  ['shape', '-BI-'],
]

test('crée un synoptique de 10 widgets liés à des points, l\'enregistre et le rouvre', async ({ page }) => {
  await uiLogin(page, ENGINEER)
  await page.getByRole('link', { name: 'Nouveau synoptique' }).click()
  await expect(page.getByLabel('Nom du synoptique')).toBeVisible()
  const name = uniqueName('E2E dix widgets')
  await page.getByLabel('Nom du synoptique').fill(name)

  for (const [index, [type, fragment]] of TEN_WIDGETS.entries()) {
    await page.locator(`.ed-palette__item[data-type="${type}"]`).click()
    // Disposition en grille 5 × 2 : chaque widget reste cliquable dans le viewer.
    const panel = page.locator('.ed-panel')
    await panel.locator('input[data-geo="x"]').fill(String(40 + (index % 5) * 370))
    await panel.locator('input[data-geo="y"]').fill(String(60 + Math.floor(index / 5) * 340))
    await panel.locator('input[data-geo="w"]').fill('320')
    await panel.locator('input[data-geo="h"]').fill('260')
    await panel.locator('[data-role="point-picker"]').first().click()
    const picker = page.getByRole('dialog', { name: /point/i })
    await picker.getByLabel('Rechercher un point').fill(fragment)
    // Les résultats de recherche remplacent l'arborescence après un court délai : on attend un point, pas un dossier.
    await picker.locator('.ed-picker__list li button:not(.folder)').first().click()
    await expect(picker).toBeHidden()
  }
  await expect(page.locator('.ed-item')).toHaveCount(10)

  await page.getByRole('button', { name: 'Enregistrer' }).click()
  await expect(page.locator('.ed-status')).toContainText('Version 1 enregistrée')
  await expect(page).toHaveURL(/#\/edit\/e2e-dix-widgets/)

  // Rouvre : rechargement complet de la page, l'éditeur relit le document enregistré.
  await page.reload()
  await expect(page.locator('.ed-item')).toHaveCount(10)
  await expect(page.locator('.ed-status')).toContainText('Version 1 enregistrée')

  // Les 10 widgets sont liés : le viewer les affiche tous, avec des données.
  const slug = new URL(page.url()).hash.split('/').pop() as string
  await page.goto(`/#/view/${slug}`)
  await expect(page.locator('.viewer [data-widget-id]')).toHaveCount(10)
  const record = await (await page.request.get(`/api/v1/synoptics/${slug}`)).json() as SavedSynoptic
  created.push(record.id)
  expect(record.doc.widgets).toHaveLength(10)
  const bound = record.doc.widgets.filter((w) => JSON.stringify(w.bind).includes('"point"') || JSON.stringify(w.bind).includes('"points"'))
  expect(bound).toHaveLength(10)
})

test('les valeurs live sont à jour dans le viewer', async ({ page, request }) => {
  await apiLogin(request, ENGINEER)
  const point = await findPoint(request, '-AI-')
  const record = await createSynoptic(
    request,
    makeDoc(uniqueName('E2E live'), [
      widget('v1', 'value', 40, 40, 300, 160, { bind: { point: point.id, format: '0.00' } }),
      widget('i1', 'indicator', 380, 40, 200, 160, { bind: { point: point.id } }),
    ]),
  )
  created.push(record.id)
  await apiLogin(page.request, OPERATOR)
  await page.goto(`/#/view/${record.slug}`)
  const number = page.locator('[data-widget-id="v1"] .number > span').first()
  await expect(number).toHaveText(/^-?\d+[.,]\d{2}$/)
  const first = await number.textContent()
  // Le simulateur fait évoluer les entrées analogiques : la valeur affichée finit par changer sans recharger.
  await expect.poll(async () => number.textContent(), { timeout: 60_000, intervals: [1000] }).not.toBe(first)
  // Et elle correspond à la dernière valeur connue de l'API (à l'arrondi et à un tick près).
  const shown = Number(((await number.textContent()) ?? '').replace(',', '.'))
  const api = (await latestValue(request, point.id)) as number
  expect(Math.abs(shown - api)).toBeLessThan(5)
})

test('un clic sur le switch change la valeur dans le simulateur', async ({ page, request }) => {
  await apiLogin(request, ENGINEER)
  const point = (await findPoint(request, '-BV-', true)) as Point
  const record = await createSynoptic(
    request,
    makeDoc(uniqueName('E2E switch'), [
      widget('sw', 'switch', 60, 60, 320, 120, { bind: { point: point.id }, priority: 8, label: 'Ventilateur' }),
    ]),
  )
  created.push(record.id)
  const release = async () => {
    await request.post(`/api/v1/points/${point.id}/write`, { data: { value: null, priority: 8 } })
  }
  try {
    await release()
    await expect.poll(() => latestValue(request, point.id), { timeout: 30_000 }).toBe(0)
    await apiLogin(page.request, OPERATOR)
    await page.goto(`/#/view/${record.slug}`)
    const toggle = page.getByRole('switch')
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
    await toggle.click()
    await page.locator('.syn-confirm').getByRole('button', { name: 'Confirmer' }).click()
    // Visible dans le viewer, puis dans la valeur lue sur l'appareil simulé.
    await expect(toggle).toHaveAttribute('aria-checked', 'true', { timeout: 30_000 })
    await expect.poll(() => latestValue(request, point.id), { timeout: 30_000 }).toBe(1)
  } finally {
    await release()
  }
})

test('un lecteur ne peut pas commander', async ({ page, request }) => {
  await apiLogin(request, ENGINEER)
  const point = await findPoint(request, '-BV-', true)
  const record = await createSynoptic(
    request,
    makeDoc(uniqueName('E2E lecteur'), [widget('sw', 'switch', 60, 60, 320, 120, { bind: { point: point.id } })]),
  )
  created.push(record.id)
  await apiLogin(page.request, { ...OPERATOR, login: process.env.E2E_VIEWER_LOGIN ?? 'e2e-viewer' })
  await page.goto(`/#/view/${record.slug}`)
  await expect(page.getByRole('switch')).toBeDisabled()
})

test('restaure une ancienne version', async ({ page, request }) => {
  await apiLogin(request, ENGINEER)
  const record = await createSynoptic(
    request,
    makeDoc(uniqueName('E2E versions'), [widget('a', 'label', 40, 40, 300, 80, { text: 'Version 1' })]),
  )
  created.push(record.id)
  await apiLogin(page.request, ENGINEER)
  await page.goto(`/#/edit/${record.slug}`)
  await expect(page.locator('.ed-item')).toHaveCount(1)

  await page.locator('.ed-palette__item[data-type="gauge"]').click()
  await page.getByRole('button', { name: 'Enregistrer' }).click()
  await expect(page.locator('.ed-status')).toContainText('Version 2 enregistrée')
  await expect(page.locator('.ed-item')).toHaveCount(2)

  await page.getByRole('button', { name: 'Versions' }).click()
  const dialog = page.getByRole('dialog', { name: 'Versions' })
  await expect(dialog.locator('li[data-version="2"] .badge')).toHaveText('actuelle')
  await dialog.getByRole('button', { name: 'Restaurer la version 1' }).click()
  await page.locator('.syn-confirm').getByRole('button', { name: 'Confirmer' }).click()
  await expect(dialog).toBeHidden()

  // La restauration crée la version 3 ; l'historique reste intact.
  await expect(page.locator('.ed-item')).toHaveCount(1)
  await expect(page.locator('.ed-status')).toContainText('Version 3')
  const versions = (await (await request.get(`/api/v1/synoptics/${record.slug}/versions`)).json()) as { version: number }[]
  expect(versions.map((v) => v.version).sort()).toEqual([1, 2, 3])
  const current = (await (await request.get(`/api/v1/synoptics/${record.slug}`)).json()) as SavedSynoptic
  expect(current.version).toBe(3)
  expect(current.doc.widgets).toHaveLength(1)
})

const SCREENS = [
  { name: 'écran 1920×1080', viewport: { width: 1920, height: 1080 } },
  { name: 'tablette paysage 1024×768', viewport: { width: 1024, height: 768 } },
  { name: 'tablette portrait 768×1024', viewport: { width: 768, height: 1024 } },
]

for (const screen of SCREENS) {
  test.describe(`affichage sur ${screen.name}`, () => {
    test.use({ viewport: screen.viewport })

    test('le synoptique tient à l\'écran, sans déformation ni défilement', async ({ page, request }, info) => {
      await apiLogin(request, ENGINEER)
      const point = await findPoint(request, '-AI-')
      const record = await createSynoptic(
        request,
        makeDoc(uniqueName('E2E écran'), [
          widget('tl', 'value', 0, 0, 400, 200, { bind: { point: point.id }, label: 'Haut gauche' }),
          widget('tr', 'gauge', 1520, 0, 400, 300, { bind: { point: point.id } }),
          widget('bl', 'label', 0, 880, 400, 200, { text: 'Bas gauche' }),
          widget('br', 'indicator', 1520, 880, 400, 200, { bind: { point: point.id } }),
        ]),
      )
      created.push(record.id)
      await apiLogin(page.request, OPERATOR)
      await page.goto(`/#/view/${record.slug}`)
      await expect(page.locator('.viewer [data-widget-id]')).toHaveCount(4)

      const size = page.viewportSize() as { width: number; height: number }
      const canvas = (await page.locator('.viewer__canvas').boundingBox()) as { x: number; y: number; width: number; height: number }
      // Le canevas 16/9 est mis à l'échelle sans déformation…
      expect(canvas.width / canvas.height).toBeCloseTo(1920 / 1080, 1)
      // …et reste entièrement visible.
      expect(canvas.x).toBeGreaterThanOrEqual(-1)
      expect(canvas.y).toBeGreaterThanOrEqual(-1)
      expect(canvas.x + canvas.width).toBeLessThanOrEqual(size.width + 1)
      expect(canvas.y + canvas.height).toBeLessThanOrEqual(size.height + 1)
      // Chaque widget d'angle est à l'écran.
      for (const id of ['tl', 'tr', 'bl', 'br']) {
        const box = (await page.locator(`[data-widget-id="${id}"]`).boundingBox()) as { x: number; y: number; width: number; height: number }
        expect(box.width).toBeGreaterThan(20)
        expect(box.x + box.width).toBeLessThanOrEqual(size.width + 1)
        expect(box.y + box.height).toBeLessThanOrEqual(size.height + 1)
      }
      const overflow = await page.evaluate(() => ({
        x: document.documentElement.scrollWidth - window.innerWidth,
        y: document.documentElement.scrollHeight - window.innerHeight,
      }))
      expect(overflow.x).toBeLessThanOrEqual(0)
      expect(overflow.y).toBeLessThanOrEqual(0)
      await info.attach(`viewer ${screen.name}`, { body: await page.screenshot(), contentType: 'image/png' })
    })
  })
}
