/** Administration : comptes utilisateurs et journal d'audit (rôle admin). */

import { ApiError } from '../api/client'
import type { AdminApi, AdminUser, AuditEntry, RoleInfo } from '../api/types'
import { element } from '../widgets/base'

export interface AdminDeps {
  api: AdminApi
  /** Compte connecté : on ne peut ni le rétrograder, ni le désactiver, ni le supprimer. */
  currentUserId: string
  confirm: (message: string) => Promise<boolean>
  /** Saisie d'un mot de passe (fenêtre modale) ; `null` si l'utilisateur annule. */
  askPassword?: (login: string) => Promise<string | null>
}

const MIN_PASSWORD = 10
const AUDIT_LIMIT = 200

const dateFormat = new Intl.DateTimeFormat('fr-FR', { dateStyle: 'short', timeStyle: 'medium' })

/** Fenêtre modale de saisie d'un nouveau mot de passe. */
export function askPasswordDialog(login: string): Promise<string | null> {
  return new Promise((resolve) => {
    const previous = document.activeElement as HTMLElement | null
    const overlay = element('div', 'modal')
    overlay.setAttribute('role', 'dialog')
    overlay.setAttribute('aria-modal', 'true')
    overlay.setAttribute('aria-label', `Nouveau mot de passe de ${login}`)
    const form = element('form', 'modal__box')
    const label = element('label', '', `Nouveau mot de passe de ${login} (${MIN_PASSWORD} caractères au moins)`)
    const input = element('input')
    input.type = 'password'
    input.autocomplete = 'new-password'
    input.minLength = MIN_PASSWORD
    input.required = true
    label.append(input)
    const actions = element('div', 'modal__actions')
    const cancel = element('button', '', 'Annuler')
    cancel.type = 'button'
    const ok = element('button', 'primary', 'Enregistrer')
    ok.type = 'submit'
    actions.append(cancel, ok)
    form.append(label, actions)
    overlay.append(form)
    const close = (value: string | null) => {
      overlay.remove()
      document.removeEventListener('keydown', onKey, true)
      previous?.focus?.()
      resolve(value)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        close(null)
      }
    }
    form.addEventListener('submit', (event) => {
      event.preventDefault()
      if (input.value.length >= MIN_PASSWORD) close(input.value)
    })
    cancel.addEventListener('click', () => close(null))
    document.addEventListener('keydown', onKey, true)
    document.body.append(overlay)
    input.focus()
  })
}

function describe(error: unknown): string {
  return error instanceof ApiError || error instanceof Error ? error.message : String(error)
}

export function mountAdmin(container: HTMLElement, deps: AdminDeps): { destroy(): void } {
  const ask = deps.askPassword ?? askPasswordDialog
  const page = element('section', 'page page--wide')
  const message = element('p', 'list__error')
  message.setAttribute('role', 'alert')
  const usersSection = element('section', 'admin__section')
  const auditSection = element('section', 'admin__section')
  page.append(message, usersSection, auditSection)
  container.append(page)
  let destroyed = false
  let roles: RoleInfo[] = []

  const fail = (error: unknown) => {
    message.textContent = describe(error)
  }

  // -- utilisateurs ------------------------------------------------------------------------
  async function loadUsers(): Promise<void> {
    try {
      const users = await deps.api.users()
      if (!destroyed) renderUsers(users)
    } catch (error) {
      if (!destroyed) fail(error)
    }
  }

  function renderUsers(users: AdminUser[]): void {
    usersSection.replaceChildren(element('h2', '', 'Utilisateurs'), createForm())
    const table = element('table', 'admin__table')
    const head = element('tr')
    for (const title of ['Identifiant', 'Rôle', 'Actif', 'État', 'Actions']) head.append(element('th', '', title))
    const thead = element('thead')
    thead.append(head)
    table.append(thead)
    const body = element('tbody')
    for (const user of users) body.append(userRow(user))
    table.append(body)
    usersSection.append(table)
  }

  function userRow(user: AdminUser): HTMLElement {
    const self = user.id === deps.currentUserId
    const row = element('tr')
    row.dataset.login = user.login
    row.append(element('td', '', user.login + (self ? ' (vous)' : '')))

    const roleCell = element('td')
    const select = element('select')
    select.setAttribute('aria-label', `Rôle de ${user.login}`)
    for (const role of roles) {
      const option = element('option', '', role.name)
      option.value = role.name
      option.title = role.description
      select.append(option)
    }
    select.value = user.role
    select.disabled = self
    select.addEventListener('change', () => void change(user, { role: select.value }, `${user.login} : rôle ${select.value}`))
    roleCell.append(select)
    row.append(roleCell)

    const activeCell = element('td')
    const active = element('input')
    active.type = 'checkbox'
    active.checked = user.active
    active.disabled = self
    active.setAttribute('aria-label', `${user.login} actif`)
    active.addEventListener('change', () => void change(user, { active: active.checked }, `${user.login} ${active.checked ? 'activé' : 'désactivé'}`))
    activeCell.append(active)
    row.append(activeCell)

    row.append(element('td', user.locked ? 'is-locked' : '', user.locked ? 'Verrouillé' : user.active ? 'Normal' : 'Désactivé'))

    const actions = element('td', 'admin__actions')
    if (user.locked) {
      const unlock = element('button', '', 'Déverrouiller')
      unlock.type = 'button'
      unlock.addEventListener('click', async () => {
        try {
          await deps.api.unlockUser(user.id)
          message.textContent = ''
          await loadUsers()
        } catch (error) {
          fail(error)
        }
      })
      actions.append(unlock)
    }
    const reset = element('button', '', 'Mot de passe…')
    reset.type = 'button'
    reset.addEventListener('click', async () => {
      const password = await ask(user.login)
      if (password !== null) await change(user, { password }, `Mot de passe de ${user.login} modifié : ses sessions sont fermées.`)
    })
    actions.append(reset)
    if (!self) {
      const remove = element('button', 'danger', 'Supprimer')
      remove.type = 'button'
      remove.addEventListener('click', async () => {
        if (!(await deps.confirm(`Supprimer définitivement le compte « ${user.login} » ?`))) return
        try {
          await deps.api.deleteUser(user.id)
          message.textContent = ''
          await loadUsers()
        } catch (error) {
          fail(error)
        }
      })
      actions.append(remove)
    }
    row.append(actions)
    return row
  }

  async function change(user: AdminUser, patch: { role?: string; active?: boolean; password?: string }, done: string): Promise<void> {
    try {
      await deps.api.updateUser(user.id, patch)
      message.textContent = ''
      status.textContent = done
    } catch (error) {
      fail(error)
    }
    await loadUsers() // reflète l'état réel du serveur, y compris après un refus
  }

  const status = element('p', 'admin__status')
  status.setAttribute('role', 'status')

  function createForm(): HTMLElement {
    const form = element('form', 'admin__form')
    const field = (label: string, input: HTMLInputElement | HTMLSelectElement) => {
      const wrap = element('label', '', label)
      wrap.append(input)
      return wrap
    }
    const login = element('input')
    login.type = 'text'
    login.required = true
    login.maxLength = 64
    login.pattern = '[A-Za-z0-9][A-Za-z0-9._@\\-]{0,63}'
    login.title = 'Lettres, chiffres, points, tirets et @'
    login.autocomplete = 'off'
    const password = element('input')
    password.type = 'password'
    password.required = true
    password.minLength = MIN_PASSWORD
    password.autocomplete = 'new-password'
    const role = element('select')
    for (const r of roles) {
      const option = element('option', '', r.name)
      option.value = r.name
      role.append(option)
    }
    role.value = 'viewer'
    const submit = element('button', 'primary', 'Créer le compte')
    submit.type = 'submit'
    form.append(field('Identifiant', login), field('Mot de passe', password), field('Rôle', role), submit, status)
    form.addEventListener('submit', async (event) => {
      event.preventDefault()
      submit.disabled = true
      try {
        await deps.api.createUser(login.value.trim(), password.value, role.value)
        message.textContent = ''
        status.textContent = `Compte ${login.value.trim()} créé.`
        await loadUsers()
      } catch (error) {
        fail(error)
        password.value = ''
        submit.disabled = false
      }
    })
    return form
  }

  // -- journal d'audit ---------------------------------------------------------------------
  const filter = element('input')
  filter.type = 'search'
  filter.placeholder = 'Action ou préfixe (ex. user.)'
  filter.setAttribute('aria-label', "Filtrer le journal par action")
  const refresh = element('button', '', 'Actualiser')
  refresh.type = 'button'
  const auditTable = element('table', 'admin__table')
  auditTable.setAttribute('aria-label', "Journal d'audit")
  const auditHead = element('div', 'admin__auditbar')
  auditHead.append(filter, refresh)
  auditSection.append(element('h2', '', "Journal d'audit"), auditHead, auditTable)

  const summary = (value: Record<string, unknown> | null) =>
    value === null ? '' : Object.entries(value).map(([key, item]) => `${key}=${String(item)}`).join(' ')

  function renderAudit(entries: AuditEntry[]): void {
    const head = element('tr')
    for (const title of ['Date', 'Action', 'Cible', 'Détail', 'Adresse IP']) head.append(element('th', '', title))
    const body = element('tbody')
    for (const entry of entries) {
      const row = element('tr')
      row.append(
        element('td', '', dateFormat.format(new Date(entry.ts))),
        element('td', '', entry.action),
        element('td', '', entry.target ?? ''),
        element('td', '', [summary(entry.before), summary(entry.after)].filter(Boolean).join(' → ')),
        element('td', '', entry.ip ?? ''),
      )
      body.append(row)
    }
    if (entries.length === 0) {
      const row = element('tr')
      const cell = element('td', 'list__empty', 'Aucune entrée.')
      cell.colSpan = 5
      row.append(cell)
      body.append(row)
    }
    auditTable.replaceChildren(element('thead'), body)
    auditTable.firstElementChild?.append(head)
  }

  async function loadAudit(): Promise<void> {
    try {
      const entries = await deps.api.audit({ action: filter.value.trim() || undefined, limit: AUDIT_LIMIT })
      if (!destroyed) renderAudit(entries)
    } catch (error) {
      if (!destroyed) fail(error)
    }
  }
  refresh.addEventListener('click', () => void loadAudit())
  filter.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') void loadAudit()
  })

  deps.api
    .roles()
    .then((list) => {
      roles = list
      return Promise.all([loadUsers(), loadAudit()])
    })
    .catch((error: unknown) => {
      if (!destroyed) fail(error)
    })

  return {
    destroy() {
      destroyed = true
      page.remove()
    },
  }
}
