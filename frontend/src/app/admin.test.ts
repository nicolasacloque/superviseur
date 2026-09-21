import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { AdminApi, AdminUser, AuditEntry, RoleInfo } from '../api/types'
import { type AdminDeps, mountAdmin } from './admin'

const ROLES: RoleInfo[] = ['viewer', 'operator', 'engineer', 'admin'].map((name, index) => ({ name, level: index + 1, description: name }))

let container: HTMLElement
let users: AdminUser[]
let audit: AuditEntry[]
let calls: string[]
let failNext: Error | null
let instance: { destroy(): void }

const api: AdminApi = {
  users: async () => users.map((u) => ({ ...u })),
  roles: async () => ROLES,
  createUser: async (login, _password, role) => {
    calls.push(`create ${login} ${role}`)
    if (failNext) throw failNext
    const created = { id: `id-${login}`, login, role, active: true, locked: false }
    users.push(created)
    return created
  },
  updateUser: async (id, patch) => {
    calls.push(`update ${id} ${JSON.stringify(patch)}`)
    if (failNext) throw failNext
    const user = users.find((u) => u.id === id) as AdminUser
    Object.assign(user, { role: patch.role ?? user.role, active: patch.active ?? user.active })
    return user
  },
  unlockUser: async (id) => {
    calls.push(`unlock ${id}`)
    const user = users.find((u) => u.id === id) as AdminUser
    user.locked = false
    return user
  },
  deleteUser: async (id) => {
    calls.push(`delete ${id}`)
    users = users.filter((u) => u.id !== id)
  },
  audit: async (query) => {
    calls.push(`audit ${query?.action ?? ''}`)
    return audit
  },
}

function open(overrides: Partial<AdminDeps> = {}): void {
  instance = mountAdmin(container, { api, currentUserId: 'id-root', confirm: async () => true, askPassword: async () => 'nouveau-secret-123', ...overrides })
}
const rows = () => [...container.querySelectorAll<HTMLElement>('.admin__table tbody tr[data-login]')]
const row = (login: string) => container.querySelector<HTMLElement>(`tr[data-login="${login}"]`)!
const button = (scope: ParentNode, label: string) => [...scope.querySelectorAll('button')].find((b) => b.textContent === label)
const ready = () => vi.waitFor(() => expect(rows().length).toBeGreaterThan(0))

beforeEach(() => {
  document.body.innerHTML = ''
  container = document.createElement('div')
  document.body.append(container)
  users = [
    { id: 'id-root', login: 'root', role: 'admin', active: true, locked: false },
    { id: 'id-op', login: 'op', role: 'operator', active: true, locked: true },
  ]
  audit = [{ id: 1, ts: '2026-09-21T10:00:00Z', user_id: 'id-root', action: 'auth.login', target: 'root', before: null, after: { result: 'ok' }, ip: '10.0.0.5' }]
  calls = []
  failNext = null
})
afterEach(() => instance?.destroy())

describe('utilisateurs', () => {
  it('liste les comptes, signale le verrouillage et protège le compte courant', async () => {
    open()
    await ready()
    expect(rows()).toHaveLength(2)
    expect(row('root').textContent).toContain('(vous)')
    expect(row('root').querySelector('select')!.disabled).toBe(true)
    expect(row('root').querySelector<HTMLInputElement>('input[type=checkbox]')!.disabled).toBe(true)
    expect(button(row('root'), 'Supprimer')).toBeUndefined()
    expect(row('op').querySelector('.is-locked')!.textContent).toBe('Verrouillé')
    expect(button(row('op'), 'Déverrouiller')).toBeDefined()
    expect(button(row('root'), 'Déverrouiller')).toBeUndefined()
  })

  it('change le rôle et désactive un compte', async () => {
    open()
    await ready()
    const select = row('op').querySelector('select')!
    select.value = 'engineer'
    select.dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect(calls).toContain('update id-op {"role":"engineer"}'))
    await vi.waitFor(() => expect(row('op').querySelector('select')!.value).toBe('engineer'))
    const active = row('op').querySelector<HTMLInputElement>('input[type=checkbox]')!
    active.checked = false
    active.dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect(calls).toContain('update id-op {"active":false}'))
  })

  it("affiche le refus du serveur et rétablit l'état réel", async () => {
    open()
    await ready()
    failNext = new ApiError(409, 'il doit rester un administrateur actif')
    const select = row('op').querySelector('select')!
    select.value = 'admin'
    select.dispatchEvent(new Event('change'))
    await vi.waitFor(() => expect(container.querySelector('.list__error')!.textContent).toContain('administrateur actif'))
    await vi.waitFor(() => expect(row('op').querySelector('select')!.value).toBe('operator'))
  })

  it('déverrouille un compte', async () => {
    open()
    await ready()
    button(row('op'), 'Déverrouiller')!.click()
    await vi.waitFor(() => expect(calls).toContain('unlock id-op'))
    await vi.waitFor(() => expect(button(row('op'), 'Déverrouiller')).toBeUndefined())
  })

  it('réinitialise un mot de passe sans le garder à l\'écran', async () => {
    open()
    await ready()
    button(row('op'), 'Mot de passe…')!.click()
    await vi.waitFor(() => expect(calls.some((c) => c.startsWith('update id-op') && c.includes('password'))).toBe(true))
    expect(container.textContent).not.toContain('nouveau-secret-123')
  })

  it('annuler la saisie du mot de passe ne modifie rien', async () => {
    open({ askPassword: async () => null })
    await ready()
    button(row('op'), 'Mot de passe…')!.click()
    await new Promise((r) => setTimeout(r, 0))
    expect(calls.filter((c) => c.startsWith('update'))).toEqual([])
  })

  it('crée un compte', async () => {
    open()
    await ready()
    const form = container.querySelector('form.admin__form')!
    const [login, password] = form.querySelectorAll<HTMLInputElement>('input')
    login!.value = 'nouveau'
    password!.value = 'un-mot-de-passe-long'
    form.querySelector('select')!.value = 'engineer'
    form.dispatchEvent(new Event('submit', { cancelable: true }))
    await vi.waitFor(() => expect(calls).toContain('create nouveau engineer'))
    await vi.waitFor(() => expect(row('nouveau')).not.toBeNull())
  })

  it('la suppression demande confirmation', async () => {
    const confirm = vi.fn(async () => false)
    open({ confirm })
    await ready()
    button(row('op'), 'Supprimer')!.click()
    await vi.waitFor(() => expect(confirm).toHaveBeenCalled())
    expect(calls.some((c) => c.startsWith('delete'))).toBe(false)
    confirm.mockResolvedValue(true)
    button(row('op'), 'Supprimer')!.click()
    await vi.waitFor(() => expect(calls).toContain('delete id-op'))
    await vi.waitFor(() => expect(rows()).toHaveLength(1))
  })
})

describe("journal d'audit", () => {
  it('affiche les entrées et filtre par action', async () => {
    open()
    await ready()
    await vi.waitFor(() => expect(container.querySelector('[aria-label="Journal d\'audit"] tbody')!.textContent).toContain('auth.login'))
    expect(container.textContent).toContain('result=ok')
    expect(container.textContent).toContain('10.0.0.5')
    const filter = container.querySelector<HTMLInputElement>('input[type=search]')!
    filter.value = 'user.create'
    filter.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    await vi.waitFor(() => expect(calls).toContain('audit user.create'))
  })

  it("indique quand le journal est vide", async () => {
    audit = []
    open()
    await vi.waitFor(() => expect(container.textContent).toContain('Aucune entrée.'))
  })
})
