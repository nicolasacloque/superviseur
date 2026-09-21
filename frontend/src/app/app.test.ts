import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { AlarmEvent, LiveApi, LiveSample, LiveState, PointInfo, SessionUser, SynopticRecord, SynopticSummary } from '../api/types'
import { newDoc, type SynopticDoc } from '../synoptic/model'
import { type AppApi, type AppInstance, mountApp } from './app'

class FakeLive implements LiveApi {
  state: LiveState = 'open'
  subscribe(_p: string[], _l: (s: LiveSample) => void): () => void {
    return () => undefined
  }
  subscribeAlarms(_l: (e: AlarmEvent) => void): () => void {
    return () => undefined
  }
  onState(): () => void {
    return () => undefined
  }
}

const record = (slug: string, version = 1): SynopticRecord => ({
  id: `id-${slug}`, name: `Synoptique ${slug}`, slug, version, updated_at: '2026-09-21T10:00:00Z', doc: newDoc(`Synoptique ${slug}`),
})  // fmt: skip

let container: HTMLElement
let apps: AppInstance[]
let session: SessionUser | null
let loginError: Error | null
let loggedOut: number
let deleted: string[]
let created: SynopticDoc[]
let location: { hash: string }
let hashListener: (() => void) | null
let store: Record<string, SynopticRecord>

function makeApi(): AppApi {
  return {
    login: async (login: string) => {
      if (loginError) throw loginError
      session = { id: 'u', login, role: 'engineer' }
    },
    logout: async () => {
      loggedOut++
      session = null
    },
    me: async () => {
      if (!session) throw new ApiError(401, 'authentification requise')
      return session
    },
    history: async () => ({ point_id: '', bucket: null, truncated: false, items: [] }),
    alarms: async () => ({ items: [], total: 0 }),
    acknowledge: async () => { throw new Error('non utilisé') },
    point: async (id: string): Promise<PointInfo> => ({ id, name: 'Temp', unit: '°C', path: null }),
    write: async () => undefined,
    tree: async () => ({ path: '', folders: [], points: [] }),
    search: async () => [],
    synoptics: async (): Promise<SynopticSummary[]> => Object.values(store),
    synoptic: async (slug: string) => {
      const found = store[slug]
      if (!found) throw new ApiError(404, 'introuvable')
      return found
    },
    createSynoptic: async (doc: SynopticDoc) => {
      created.push(doc)
      const rec = { ...record('nouveau'), doc, name: doc.name }
      store['nouveau'] = rec
      return rec
    },
    saveSynoptic: async () => { throw new Error('non utilisé') },
    deleteSynoptic: async (id: string) => { deleted.push(id) },
    versions: async () => [],
    version: async () => { throw new Error('non utilisé') },
    restoreVersion: async () => { throw new Error('non utilisé') },
  } as unknown as AppApi  // fmt: skip
}

function start(hash = ''): AppInstance {
  location.hash = hash
  const app = mountApp(container, {
    api: makeApi(), live: new FakeLive(), location,
    onHashChange: (listener) => {
      hashListener = listener
      return () => { hashListener = null }
    },
  })  // fmt: skip
  apps.push(app)
  return app
}
const navigate = (hash: string) => {
  location.hash = hash
  hashListener?.()
}
const settle = () => vi.waitFor(() => expect(container.querySelector('.app-note, .login, .list__items, .viewer, .ed')).not.toBeNull())
const text = () => container.textContent ?? ''
const button = (label: string) => [...container.querySelectorAll<HTMLElement>('button, a')].find((b) => b.textContent === label)

beforeEach(() => {
  document.body.innerHTML = ''
  container = document.createElement('div')
  document.body.append(container)
  apps = []
  session = { id: 'u', login: 'alice', role: 'engineer' }
  loginError = null
  loggedOut = 0
  deleted = []
  created = []
  location = { hash: '' }
  hashListener = null
  store = { 'cta-1': record('cta-1', 3), 'cta-2': record('cta-2') }
})
afterEach(() => apps.forEach((a) => a.destroy()))

describe('session', () => {
  it('sans session, affiche la connexion', async () => {
    session = null
    start()
    await settle()
    expect(container.querySelector('form.login')).not.toBeNull()
    expect(location.hash).toBe('#/login')
  })

  it('une connexion réussie ouvre la liste', async () => {
    session = null
    start()
    await settle()
    const inputs = container.querySelectorAll<HTMLInputElement>('form.login input')
    inputs[0]!.value = 'alice'
    inputs[1]!.value = 'secret'
    container.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await vi.waitFor(() => expect(container.querySelector('.list__items')).not.toBeNull())
    expect(location.hash).toBe('#/')
    expect(text()).toContain('alice (engineer)')
  })

  it('un mauvais mot de passe affiche un message sans se connecter', async () => {
    session = null
    loginError = new ApiError(401, 'identifiants invalides')
    start()
    await settle()
    const inputs = container.querySelectorAll<HTMLInputElement>('form.login input')
    inputs[0]!.value = 'alice'
    inputs[1]!.value = 'mauvais'
    container.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await vi.waitFor(() => expect(container.querySelector('.login .error')!.textContent).toContain('incorrect'))
    expect(inputs[1]!.value).toBe('')
  })

  it('une erreur serveur à la vérification de session est signalée', async () => {
    const api = makeApi()
    api.me = async () => { throw new ApiError(500, 'boom') }
    mountApp(container, { api, live: new FakeLive(), location, onHashChange: () => () => undefined })
    await settle()
    expect(text()).toContain('injoignable')
  })

  it('la déconnexion revient à la connexion', async () => {
    start()
    await settle()
    button('Se déconnecter')!.click()
    await vi.waitFor(() => expect(container.querySelector('form.login')).not.toBeNull())
    expect(loggedOut).toBe(1)
  })
})

describe('liste', () => {
  it('un ingénieur peut créer, modifier et supprimer', async () => {
    start('#/')
    await settle()
    expect(container.querySelectorAll('.list__items li')).toHaveLength(2)
    expect(button('Nouveau synoptique')).toBeDefined()
    expect(container.querySelectorAll('.list__items a.btn')).toHaveLength(2)
  })

  it('un opérateur ne voit que la consultation', async () => {
    session = { id: 'u', login: 'bob', role: 'operator' }
    start('#/')
    await settle()
    expect(button('Nouveau synoptique')).toBeUndefined()
    expect(button('Modifier')).toBeUndefined()
    expect(button('Supprimer')).toBeUndefined()
  })

  it('la suppression demande une confirmation', async () => {
    start('#/')
    await settle()
    button('Supprimer')!.click()
    await vi.waitFor(() => expect(document.querySelector('.syn-confirm')).not.toBeNull())
    ;(document.querySelector('.syn-confirm .primary') as HTMLButtonElement).click()
    await vi.waitFor(() => expect(deleted).toEqual(['id-cta-1']))
    expect(container.querySelectorAll('.list__items li')).toHaveLength(1)
  })

  it('annuler la confirmation ne supprime rien', async () => {
    start('#/')
    await settle()
    button('Supprimer')!.click()
    await vi.waitFor(() => expect(document.querySelector('.syn-confirm')).not.toBeNull())
    ;(document.querySelector('.syn-confirm button') as HTMLButtonElement).click()
    await new Promise((r) => setTimeout(r, 0))
    expect(deleted).toEqual([])
  })

  it('liste vide', async () => {
    store = {}
    start('#/')
    await settle()
    expect(text()).toContain('Aucun synoptique')
  })
})

describe('consultation et édition', () => {
  it('affiche un synoptique', async () => {
    start('#/view/cta-1')
    await vi.waitFor(() => expect(container.querySelector('.viewer')).not.toBeNull())
    expect(text()).toContain('Synoptique cta-1')
  })

  it('synoptique inexistant', async () => {
    start('#/view/absent')
    await settle()
    expect(text()).toContain('n’existe pas')
  })

  it('la navigation entre synoptiques change de page', async () => {
    start('#/view/cta-1')
    await vi.waitFor(() => expect(container.querySelector('.viewer')).not.toBeNull())
    navigate('#/view/cta-2')
    await vi.waitFor(() => expect(text()).toContain('Synoptique cta-2'))
  })

  it("l'édition est refusée à un opérateur", async () => {
    session = { id: 'u', login: 'bob', role: 'operator' }
    start('#/edit/cta-1')
    await settle()
    expect(text()).toContain('réservée aux ingénieurs')
    expect(container.querySelector('.ed')).toBeNull()
  })

  it("ouvre l'éditeur sur un synoptique existant", async () => {
    start('#/edit/cta-1')
    await vi.waitFor(() => expect(container.querySelector('.ed')).not.toBeNull())
    expect(text()).toContain('Version 3 enregistrée')
  })

  it("la création met à jour l'adresse sans remonter l'éditeur", async () => {
    start('#/edit/new')
    await vi.waitFor(() => expect(container.querySelector('.ed')).not.toBeNull())
    const editor = container.querySelector('.ed')!
    ;(container.querySelector('.ed-palette__item') as HTMLButtonElement).click()
    ;(button('Enregistrer') as HTMLButtonElement).click()
    await vi.waitFor(() => expect(location.hash).toBe('#/edit/nouveau'))
    hashListener?.() // l'événement `hashchange` que le navigateur émettrait
    await new Promise((r) => setTimeout(r, 0))
    expect(container.querySelector('.ed')).toBe(editor) // même éditeur, non recréé
    expect(created).toHaveLength(1)
  })

  it("une adresse inconnue ramène à la liste", async () => {
    start('#/n-importe-quoi')
    await settle()
    expect(container.querySelector('.list__items')).not.toBeNull()
  })
})
