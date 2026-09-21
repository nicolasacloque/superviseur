/** Sélecteur de point : arborescence des chemins + recherche, avec aperçu de la valeur courante. */

import type { PointInfo, PointsApi } from '../api/types'
import { element, formatNumber } from '../widgets/base'

const SEARCH_DELAY_MS = 250

/** Ouvre le sélecteur ; résout avec le point choisi, ou `null` si l'utilisateur ferme sans choisir. */
export function openPointPicker(api: PointsApi, options: { title?: string } = {}): Promise<PointInfo | null> {
  return new Promise((resolve) => {
    const previous = document.activeElement as HTMLElement | null
    const overlay = element('div', 'ed-modal')
    overlay.setAttribute('role', 'dialog')
    overlay.setAttribute('aria-modal', 'true')
    overlay.setAttribute('aria-label', options.title ?? 'Choisir un point')
    const box = element('div', 'ed-modal__box ed-picker')
    const title = element('h2', '', options.title ?? 'Choisir un point')
    const search = element('input')
    search.type = 'search'
    search.placeholder = 'Rechercher par nom, chemin ou tag…'
    search.setAttribute('aria-label', 'Rechercher un point')
    const crumbs = element('nav', 'ed-picker__crumbs')
    crumbs.setAttribute('aria-label', 'Chemin')
    const list = element('ul', 'ed-picker__list')
    const status = element('div', 'ed-picker__status')
    status.setAttribute('role', 'status')
    const close = element('button', '', 'Fermer')
    close.type = 'button'
    box.append(title, search, crumbs, list, status, close)
    overlay.append(box)

    let path = ''
    let loadSeq = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    const finish = (point: PointInfo | null) => {
      clearTimeout(timer)
      overlay.remove()
      document.removeEventListener('keydown', onKey, true)
      previous?.focus?.()
      resolve(point)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        finish(null)
      }
    }

    const pointRow = (point: PointInfo): HTMLElement => {
      const item = element('li')
      const button = element('button')
      button.type = 'button'
      const name = element('span', 'name', point.name)
      const where = element('span', 'path', point.path ?? '')
      const latest = point.latest
      const value = element('span', 'value', latest ? `${formatNumber(latest.value)}${point.unit ? ` ${point.unit}` : ''}` : '—')
      button.append(name, where, value)
      button.addEventListener('click', () => finish(point))
      item.append(button)
      return item
    }

    const showTree = async () => {
      const seq = ++loadSeq
      status.textContent = 'Chargement…'
      try {
        const tree = await api.tree(path)
        if (seq !== loadSeq) return
        list.replaceChildren(
          ...tree.folders.map((folder) => {
            const item = element('li')
            const button = element('button', 'folder')
            button.type = 'button'
            button.append(element('span', 'name', `📁 ${folder.name}`), element('span', 'count', `${folder.count} points`))
            button.addEventListener('click', () => {
              path = folder.path
              renderCrumbs()
              void showTree()
            })
            item.append(button)
            return item
          }),
          ...tree.points.map(pointRow),
        )
        status.textContent = tree.folders.length + tree.points.length === 0 ? 'Aucun point à ce niveau.' : ''
      } catch (error) {
        if (seq === loadSeq) status.textContent = `Chargement impossible : ${error instanceof Error ? error.message : error}`
      }
    }

    const showSearch = async (query: string) => {
      const seq = ++loadSeq
      status.textContent = 'Recherche…'
      try {
        const found = await api.search(query, 50)
        if (seq !== loadSeq) return
        list.replaceChildren(...found.map(pointRow))
        status.textContent = found.length === 0 ? 'Aucun résultat.' : found.length === 50 ? 'Les 50 premiers résultats : affinez la recherche.' : ''
      } catch (error) {
        if (seq === loadSeq) status.textContent = `Recherche impossible : ${error instanceof Error ? error.message : error}`
      }
    }

    function renderCrumbs(): void {
      const parts = path ? path.split('/') : []
      const buttons: HTMLElement[] = []
      const root = element('button', '', 'Tous les points')
      root.type = 'button'
      root.addEventListener('click', () => {
        path = ''
        renderCrumbs()
        void showTree()
      })
      buttons.push(root)
      parts.forEach((part, index) => {
        const target = parts.slice(0, index + 1).join('/')
        const button = element('button', '', part)
        button.type = 'button'
        button.addEventListener('click', () => {
          path = target
          renderCrumbs()
          void showTree()
        })
        buttons.push(element('span', 'sep', '›'), button)
      })
      crumbs.replaceChildren(...buttons)
      crumbs.hidden = search.value.trim() !== ''
    }

    search.addEventListener('input', () => {
      clearTimeout(timer)
      timer = setTimeout(() => {
        const query = search.value.trim()
        crumbs.hidden = query !== ''
        if (query) void showSearch(query)
        else void showTree()
      }, SEARCH_DELAY_MS)
    })
    close.addEventListener('click', () => finish(null))
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) finish(null)
    })
    document.addEventListener('keydown', onKey, true)
    document.body.append(overlay)
    renderCrumbs()
    search.focus()
    void showTree()
  })
}
