/** Coquille de l'application : session, routage, barre supérieure. */

import { ApiError } from '../api/client'
import type { LiveApi, SessionUser, SynopticRecord } from '../api/types'
import { mountEditor, type EditorApi } from '../editor/editor'
import { mountViewer } from '../viewer/viewer'
import { dialogConfirm } from '../viewer/confirm'
import { element } from '../widgets/base'
import { mountList } from './list'
import { mountLogin } from './login'
import { hrefFor, parseRoute, type Route } from './router'
import { injectAppStyles } from './styles'

export interface AppApi extends EditorApi {
  login(login: string, password: string): Promise<void>
  logout(): Promise<void>
  me(): Promise<SessionUser>
}

export interface AppDeps {
  api: AppApi
  live: LiveApi
  /** Lecture / écriture de l'adresse : injectable pour les tests. */
  location?: { hash: string }
  onHashChange?: (listener: () => void) => () => void
}

const ENGINEER_ROLES = new Set(['engineer', 'admin'])

export interface AppInstance {
  destroy(): void
}

export function mountApp(container: HTMLElement, deps: AppDeps): AppInstance {
  injectAppStyles()
  const where = deps.location ?? window.location
  const root = element('div', 'app')
  container.append(root)
  let user: SessionUser | null = null
  let current: { destroy(): void } | null = null
  let generation = 0
  let destroyed = false

  const listen =
    deps.onHashChange ??
    ((listener: () => void) => {
      window.addEventListener('hashchange', listener)
      return () => window.removeEventListener('hashchange', listener)
    })
  // Adresse posée par l'application elle-même sans vouloir recharger la page affichée.
  let silentHash: string | null = null
  const stopListening = listen(() => {
    if (silentHash !== null && where.hash === silentHash) {
      silentHash = null
      return
    }
    silentHash = null
    void show()
  })

  const go = (route: Route) => {
    where.hash = hrefFor(route)
  }

  function clear(): void {
    current?.destroy()
    current = null
    root.replaceChildren()
  }

  function bar(title: string, actions: HTMLElement[] = []): HTMLElement {
    const header = element('header', 'app-bar')
    const heading = element('h1', '', title)
    header.append(heading, ...actions)
    if (user) {
      header.append(element('span', 'who', `${user.login} (${user.role})`))
      const logout = element('button', '', 'Se déconnecter')
      logout.type = 'button'
      logout.addEventListener('click', () => {
        void deps.api.logout().finally(() => {
          user = null
          go({ name: 'login' })
          void show()
        })
      })
      header.append(logout)
    }
    return header
  }

  function link(label: string, route: Route, primary = false): HTMLAnchorElement {
    const a = element('a', primary ? 'btn primary' : 'btn', label)
    a.href = hrefFor(route)
    return a
  }

  async function ensureSession(): Promise<boolean> {
    if (user) return true
    try {
      user = await deps.api.me()
      return true
    } catch (failure) {
      if (failure instanceof ApiError && failure.status !== 401) throw failure
      return false
    }
  }

  async function show(): Promise<void> {
    if (destroyed) return
    const mine = ++generation
    const route = parseRoute(where.hash)
    let signedIn: boolean
    try {
      signedIn = await ensureSession()
    } catch {
      if (mine === generation) {
        clear()
        root.append(element('p', 'app-note', 'Le serveur est injoignable. Réessayez dans un instant.'))
      }
      return
    }
    if (mine !== generation) return
    clear()
    if (!signedIn) {
      if (route.name !== 'login') where.hash = hrefFor({ name: 'login' })
      const body = element('div', 'app-body')
      root.append(body)
      current = mountLogin(body, {
        login: (login, password) => deps.api.login(login, password),
        onLoggedIn: () => {
          user = null
          go({ name: 'list' })
          void show()
        },
      })
      return
    }
    if (route.name === 'login') return go({ name: 'list' })
    const canEdit = ENGINEER_ROLES.has((user as SessionUser).role)

    if (route.name === 'list') {
      root.append(bar('Superviseur'))
      const body = element('div', 'app-body')
      root.append(body)
      current = mountList(body, { api: deps.api, canEdit, confirm: dialogConfirm })
      return
    }

    let record: SynopticRecord | null = null
    if (!(route.name === 'edit' && route.slug === null)) {
      try {
        record = await deps.api.synoptic(route.slug as string)
      } catch (failure) {
        if (mine !== generation) return
        root.append(bar('Synoptique introuvable', [link('← Synoptiques', { name: 'list' })]))
        const message = failure instanceof ApiError && failure.status === 404 ? 'Ce synoptique n’existe pas.' : 'Chargement impossible.'
        root.append(element('p', 'app-note', message))
        return
      }
      if (mine !== generation) return
    }

    if (route.name === 'view' && record) {
      const actions = canEdit ? [link('Modifier', { name: 'edit', slug: record.slug })] : []
      root.append(bar(record.name, [link('← Synoptiques', { name: 'list' }), ...actions]))
      const body = element('div', 'app-body')
      root.append(body)
      current = mountViewer(body, record, {
        api: deps.api,
        live: deps.live,
        role: (user as SessionUser).role,
        navigate: (slug) => go({ name: 'view', slug }),
      })
      return
    }

    if (route.name === 'edit') {
      if (!canEdit) {
        root.append(bar('Accès refusé', [link('← Synoptiques', { name: 'list' })]))
        root.append(element('p', 'app-note', 'La modification des synoptiques est réservée aux ingénieurs.'))
        return
      }
      const body = element('div', 'app-body')
      body.style.height = '100%'
      root.append(body)
      root.classList.add('app--editing')
      current = mountEditor(body, {
        api: deps.api,
        live: deps.live,
        role: (user as SessionUser).role,
        record,
        exit: () => go(record ? { name: 'view', slug: record.slug } : { name: 'list' }),
        onSaved: (saved) => {
          record = saved
          // L'adresse passe de « nouveau » au synoptique créé, sans recharger l'éditeur.
          const target = hrefFor({ name: 'edit', slug: saved.slug })
          if (where.hash !== target) {
            silentHash = target // le changement d'adresse ne doit pas remonter l'éditeur
            where.hash = target
          }
        },
      })
    }
  }

  void show()
  return {
    destroy() {
      destroyed = true
      stopListening()
      clear()
      root.remove()
    },
  }
}
