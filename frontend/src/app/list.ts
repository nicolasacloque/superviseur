import type { SynopticsApi, SynopticSummary } from '../api/types'
import { element } from '../widgets/base'
import { hrefFor } from './router'

export interface ListDeps {
  api: SynopticsApi
  canEdit: boolean
  confirm: (message: string) => Promise<boolean>
}

export function mountList(container: HTMLElement, deps: ListDeps): { destroy(): void } {
  const page = element('section', 'page')
  const head = element('div', 'list__head')
  head.append(element('h2', '', 'Synoptiques'))
  if (deps.canEdit) {
    const create = element('a', 'btn primary', 'Nouveau synoptique')
    create.href = hrefFor({ name: 'edit', slug: null })
    head.append(create)
  }
  const error = element('p', 'list__error')
  error.setAttribute('role', 'alert')
  const list = element('ul', 'list__items')
  page.append(head, error, list)
  container.append(page)
  let destroyed = false

  const dateFormat = new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium', timeStyle: 'short' })

  function render(items: SynopticSummary[]): void {
    list.replaceChildren()
    if (items.length === 0) {
      const empty = element('li', 'list__empty', deps.canEdit ? 'Aucun synoptique : créez le premier.' : 'Aucun synoptique disponible.')
      list.append(empty)
      return
    }
    for (const item of items) {
      const row = element('li')
      row.dataset.slug = item.slug
      const open = element('a', 'name', item.name)
      open.href = hrefFor({ name: 'view', slug: item.slug })
      const meta = element('span', 'meta', `v${item.version} · ${dateFormat.format(new Date(item.updated_at))}`)
      row.append(open, meta)
      if (deps.canEdit) {
        const edit = element('a', 'btn', 'Modifier')
        edit.href = hrefFor({ name: 'edit', slug: item.slug })
        const remove = element('button', 'danger', 'Supprimer')
        remove.type = 'button'
        remove.addEventListener('click', () => {
          void deps.confirm(`Supprimer « ${item.name} » et toutes ses versions ?`).then(async (ok) => {
            if (!ok) return
            try {
              await deps.api.deleteSynoptic(item.id)
              row.remove()
              if (list.children.length === 0) render([])
            } catch (failure) {
              error.textContent = `Suppression impossible : ${failure instanceof Error ? failure.message : String(failure)}`
            }
          })
        })
        row.append(edit, remove)
      }
      list.append(row)
    }
  }

  deps.api
    .synoptics()
    .then((items) => {
      if (!destroyed) render(items)
    })
    .catch((failure: unknown) => {
      if (!destroyed) error.textContent = `Chargement impossible : ${failure instanceof Error ? failure.message : String(failure)}`
    })

  return {
    destroy() {
      destroyed = true
      page.remove()
    },
  }
}
