import { expect, type APIRequestContext, type Page } from '@playwright/test'

export interface Account {
  login: string
  password: string
}

export const ENGINEER: Account = {
  login: process.env.E2E_ENGINEER_LOGIN ?? 'e2e-engineer',
  password: process.env.E2E_PASSWORD ?? 'mot-de-passe-e2e-123',
}
export const OPERATOR: Account = {
  login: process.env.E2E_OPERATOR_LOGIN ?? 'e2e-operator',
  password: process.env.E2E_PASSWORD ?? 'mot-de-passe-e2e-123',
}

export const ADMIN: Account = {
  login: process.env.E2E_ADMIN_LOGIN ?? 'e2e-admin',
  password: process.env.E2E_PASSWORD ?? 'mot-de-passe-e2e-123',
}

const API = '/api/v1'

export interface Point {
  id: string
  name: string
  unit: string | null
  latest?: { value: number | null; status: string } | null
}

/** Ouvre une session par l'API (les cookies restent dans le contexte du navigateur). */
export async function apiLogin(request: APIRequestContext, who: Account): Promise<void> {
  const response = await request.post(`${API}/auth/login`, { data: { login: who.login, password: who.password } })
  expect(response.ok(), `connexion de ${who.login}`).toBe(true)
}

/** Premier point du simulateur dont le nom contient `fragment` (ex. « -AI- » : entrée analogique). */
export async function findPoint(request: APIRequestContext, fragment: string, writable?: boolean): Promise<Point> {
  const query = new URLSearchParams({ q: fragment, limit: '50' })
  if (writable !== undefined) query.set('writable', String(writable))
  const response = await request.get(`${API}/points?${query}`)
  expect(response.ok()).toBe(true)
  const { items } = (await response.json()) as { items: Point[] }
  expect(items.length, `un point « ${fragment} » doit exister (le simulateur tourne-t-il ?)`).toBeGreaterThan(0)
  return items[0] as Point
}

export async function latestValue(request: APIRequestContext, id: string): Promise<number | null> {
  const response = await request.get(`${API}/points/${id}`)
  expect(response.ok()).toBe(true)
  const detail = (await response.json()) as Point
  return detail.latest?.value ?? null
}

export interface SavedSynoptic {
  id: string
  slug: string
  name: string
  version: number
  doc: SynopticDoc
}

export interface SynopticDoc {
  schema: 1
  name: string
  canvas: { width: number; height: number; background: string; bg_image: string | null }
  widgets: Record<string, unknown>[]
  links: unknown[]
}

export function uniqueName(prefix: string): string {
  return `${prefix} ${Date.now().toString(36)}${Math.floor(Math.random() * 1000)}`
}

export function makeDoc(name: string, widgets: Record<string, unknown>[] = [], width = 1920, height = 1080): SynopticDoc {
  return { schema: 1, name, canvas: { width, height, background: '#0f172a', bg_image: null }, widgets, links: [] }
}

export function widget(id: string, type: string, x: number, y: number, w: number, h: number, extra: Record<string, unknown> = {}) {
  return { id, type, x, y, w, h, style: {}, bind: {}, rules: [], ...extra }
}

export async function createSynoptic(request: APIRequestContext, doc: SynopticDoc): Promise<SavedSynoptic> {
  const response = await request.post(`${API}/synoptics`, { data: { doc } })
  expect(response.ok(), await response.text()).toBe(true)
  return (await response.json()) as SavedSynoptic
}

export async function deleteSynoptic(request: APIRequestContext, id: string): Promise<void> {
  await request.delete(`${API}/synoptics/${id}`)
}

/** Connexion par le formulaire, comme un utilisateur. */
export async function uiLogin(page: Page, who: Account): Promise<void> {
  await page.goto('/')
  await page.getByLabel('Identifiant').fill(who.login)
  await page.getByLabel('Mot de passe').fill(who.password)
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await expect(page.getByRole('heading', { name: 'Synoptiques' })).toBeVisible()
}
