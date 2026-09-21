/** Historique des versions d'un synoptique : liste et restauration. */

import type { SynopticRecord, SynopticsApi, VersionInfo } from '../api/types'
import { element } from '../widgets/base'

export interface VersionsDeps {
  api: SynopticsApi
  record: SynopticRecord
  confirm: (message: string) => Promise<boolean>
  /** Appelé avec la nouvelle version créée par la restauration. */
  onRestored: (record: SynopticRecord) => void
}

const formatDate = (iso: string) =>
  new Date(iso).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })

export function openVersions(deps: VersionsDeps): void {
  const previous = document.activeElement as HTMLElement | null
  const overlay = element('div', 'ed-modal')
  overlay.setAttribute('role', 'dialog')
  overlay.setAttribute('aria-modal', 'true')
  overlay.setAttribute('aria-label', 'Versions')
  const box = element('div', 'ed-modal__box ed-versions')
  const list = element('ul', 'ed-versions__list')
  const status = element('div', 'ed-picker__status')
  status.setAttribute('role', 'status')
  const close = element('button', '', 'Fermer')
  close.type = 'button'
  box.append(element('h2', '', `Versions de « ${deps.record.name} »`), list, status, close)
  overlay.append(box)

  const finish = () => {
    overlay.remove()
    document.removeEventListener('keydown', onKey, true)
    previous?.focus?.()
  }
  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      finish()
    }
  }
  close.addEventListener('click', finish)
  overlay.addEventListener('click', (event) => {
    if (event.target === overlay) finish()
  })

  const row = (info: VersionInfo, current: number): HTMLElement => {
    const item = element('li')
    item.dataset.version = String(info.version)
    const label = element('span', '', `Version ${info.version} — ${formatDate(info.created_at)}${info.created_by ? ` — ${info.created_by}` : ''}`)
    item.append(label)
    if (info.version === current) {
      item.append(element('span', 'badge', 'actuelle'))
    } else {
      const restore = element('button', '', 'Restaurer')
      restore.type = 'button'
      restore.setAttribute('aria-label', `Restaurer la version ${info.version}`)
      restore.addEventListener('click', async () => {
        if (!(await deps.confirm(`Restaurer la version ${info.version} ? Elle deviendra la nouvelle version courante ; l'historique est conservé.`))) return
        restore.disabled = true
        try {
          const restored = await deps.api.restoreVersion(deps.record.id, info.version)
          deps.onRestored(restored)
          finish()
        } catch (error) {
          restore.disabled = false
          status.textContent = `Restauration impossible : ${error instanceof Error ? error.message : error}`
        }
      })
      item.append(restore)
    }
    return item
  }

  document.addEventListener('keydown', onKey, true)
  document.body.append(overlay)
  close.focus()
  status.textContent = 'Chargement…'
  deps.api
    .versions(deps.record.slug)
    .then((versions) => {
      list.replaceChildren(...versions.map((v) => row(v, deps.record.version)))
      status.textContent = versions.length === 0 ? 'Aucune version.' : ''
    })
    .catch((error) => {
      status.textContent = `Chargement impossible : ${error instanceof Error ? error.message : error}`
    })
}
