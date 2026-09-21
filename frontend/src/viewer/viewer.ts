/** Viewer : affiche un synoptique et le tient à jour en direct par WebSocket. */

import type { LiveApi, LiveSample, PointInfo, SynopticRecord } from '../api/types'
import { boundPoints, isDarkColor, type WidgetDef } from '../synoptic/model'
import { element, injectWidgetStyles, type WidgetApi, type WidgetContext } from '../widgets/base'
import { placeholder } from '../widgets/trend/module'
import { moduleFor } from '../widgets/registry'
import type { WidgetInstance } from '../widgets/types'
import { type Schedule, UpdateBatcher } from './batcher'
import { dialogConfirm, injectConfirmStyles } from './confirm'

/** Types qui reçoivent les valeurs du viewer ; `trend` et `alarm_list` s'abonnent eux-mêmes. */
const PUSHED = new Set(['value', 'gauge', 'indicator', 'switch', 'setpoint', 'shape'])
const WRITER_ROLES = new Set(['operator', 'engineer', 'admin'])
const DATA_IMAGE = /^data:image\/(png|jpeg|svg\+xml|webp);base64,[A-Za-z0-9+/=]+$/

export interface ViewerDeps {
  api: WidgetApi
  live: LiveApi
  role: string
  navigate: (target: string) => void
  now?: () => number
  schedule?: Schedule
  confirm?: (message: string) => Promise<boolean>
}

export interface ViewerInstance {
  /** Nombre de widgets réellement affichés (hors erreurs de rendu). */
  readonly rendered: number
  destroy(): void
}

/** Échelle qui fait tenir le canevas dans l'espace disponible, sans jamais le déformer. */
export function fitScale(stage: { width: number; height: number }, canvas: { width: number; height: number }): number {
  if (stage.width <= 0 || stage.height <= 0) return 1
  return Math.min(stage.width / canvas.width, stage.height / canvas.height)
}

export function mountViewer(container: HTMLElement, record: SynopticRecord, deps: ViewerDeps): ViewerInstance {
  injectWidgetStyles()
  injectConfirmStyles()
  injectViewerStyles()
  const doc = record.doc
  const dark = isDarkColor(doc.canvas.background)
  const theme = dark ? 'dark' : 'light'

  const root = element('div', 'viewer')
  root.dataset.state = 'ok'
  root.setAttribute('role', 'region')
  root.setAttribute('aria-label', doc.name)
  const frame = element('div', 'viewer__frame')
  const canvas = element('div', 'viewer__canvas')
  canvas.style.width = `${doc.canvas.width}px`
  canvas.style.height = `${doc.canvas.height}px`
  canvas.style.background = doc.canvas.background
  if (doc.canvas.bg_image && DATA_IMAGE.test(doc.canvas.bg_image)) {
    canvas.style.backgroundImage = `url("${doc.canvas.bg_image}")`
    canvas.style.backgroundSize = '100% 100%'
  }
  canvas.style.setProperty('--syn-ink', dark ? '#f8fafc' : '#0b0b0b')
  canvas.style.setProperty('--syn-muted', dark ? '#94a3b8' : '#64748b')
  canvas.style.setProperty('--syn-canvas', doc.canvas.background)
  const banner = element('div', 'viewer__banner', 'Connexion perdue : les valeurs affichées ne sont plus à jour')
  banner.setAttribute('role', 'status')
  banner.hidden = true
  frame.append(canvas)
  root.append(frame, banner)
  container.append(root)

  // -- échelle proportionnelle -------------------------------------------------------------
  const rescale = () => {
    const scale = fitScale({ width: root.clientWidth, height: root.clientHeight }, doc.canvas)
    canvas.style.transform = `scale(${scale})`
    frame.style.width = `${doc.canvas.width * scale}px`
    frame.style.height = `${doc.canvas.height * scale}px`
    root.dataset.scale = scale.toFixed(4)
  }
  rescale()
  const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(rescale)
  observer?.observe(root)

  // -- contexte des widgets ----------------------------------------------------------------
  const infos = new Map<string, Promise<PointInfo | null>>()
  const context: WidgetContext = {
    api: deps.api,
    live: deps.live,
    write: deps.api.write ? (point, value, priority) => deps.api.write!(point, value, priority) : undefined,
    canWrite: WRITER_ROLES.has(deps.role),
    navigate: deps.navigate,
    theme,
    confirm: deps.confirm ?? dialogConfirm,
    pointInfo: (id) => {
      let info = infos.get(id)
      if (!info) {
        info = deps.api.point ? deps.api.point(id).catch(() => null) : Promise.resolve(null)
        infos.set(id, info)
      }
      return info
    },
  }

  // -- widgets -----------------------------------------------------------------------------
  const instances = new Map<string, WidgetInstance>()
  const widgetsByPoint = new Map<string, string[]>()
  const linkTargets = new Map(doc.links.map((link) => [link.widget, link.target]))
  const cleanups: (() => void)[] = []

  for (const original of doc.widgets) {
    const widget: WidgetDef =
      original.type === 'link' && !original.target && linkTargets.has(original.id)
        ? { ...original, target: linkTargets.get(original.id) }
        : original
    const item = element('div', 'viewer__item')
    item.dataset.widgetId = widget.id
    item.dataset.type = widget.type
    Object.assign(item.style, { left: `${widget.x}px`, top: `${widget.y}px`, width: `${widget.w}px`, height: `${widget.h}px` })
    canvas.append(item)
    const module = moduleFor(widget.type)
    try {
      if (!module) throw new Error(`type de widget inconnu : ${widget.type}`)
      instances.set(widget.id, module.render(item, widget, context))
    } catch (error) {
      // Un widget en erreur n'empêche pas d'afficher le reste du synoptique.
      instances.set(widget.id, placeholder(item, widget, error instanceof Error ? error.message : 'Erreur'))
    }
    if (PUSHED.has(widget.type)) {
      for (const point of boundPoints(widget)) widgetsByPoint.set(point, [...(widgetsByPoint.get(point) ?? []), widget.id])
    }
    const target = linkTargets.get(widget.id)
    if (target && widget.type !== 'link') {
      item.classList.add('is-link')
      item.tabIndex = 0
      item.setAttribute('role', 'link')
      item.addEventListener('click', () => deps.navigate(target))
      item.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') deps.navigate(target)
      })
    }
  }

  // -- temps réel : une seule connexion pour tout le synoptique -----------------------------
  const batcher = new UpdateBatcher((widgetId, sample) => instances.get(widgetId)?.update(sample), {
    now: deps.now,
    schedule: deps.schedule,
  })
  const dispatch = (sample: LiveSample) => {
    for (const widgetId of widgetsByPoint.get(sample.point) ?? []) batcher.push(widgetId, sample)
  }
  const points = [...widgetsByPoint.keys()]
  if (points.length > 0) cleanups.push(deps.live.subscribe(points, dispatch))
  cleanups.push(
    deps.live.onState((state) => {
      const lost = state === 'closed'
      root.dataset.state = lost ? 'stale' : 'ok' // valeurs grisées tant que la connexion est perdue
      banner.hidden = !lost
    }),
  )

  return {
    get rendered() {
      return doc.widgets.length
    },
    destroy() {
      batcher.destroy()
      observer?.disconnect()
      for (const cleanup of cleanups) cleanup()
      for (const instance of instances.values()) instance.destroy()
      instances.clear()
      root.remove()
    },
  }
}

const STYLE_ID = 'syn-viewer-styles'

function injectViewerStyles(): void {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
.viewer{position:relative;display:flex;align-items:center;justify-content:center;width:100%;height:100%;overflow:hidden;background:#020617}
.viewer__frame{position:relative;flex:none;overflow:hidden}
.viewer__canvas{position:absolute;left:0;top:0;transform-origin:0 0;overflow:hidden}
.viewer__item{position:absolute;box-sizing:border-box}
.viewer__item.is-link{cursor:pointer}
.viewer__item.is-link:focus-visible{outline:2px solid #fff;outline-offset:2px}
.viewer[data-state="stale"] .viewer__canvas{filter:grayscale(1);opacity:.55;transition:opacity .2s,filter .2s}
.viewer__banner{position:absolute;left:50%;top:12px;transform:translateX(-50%);padding:6px 14px;border-radius:8px;background:#7f1d1d;color:#fff;font:14px system-ui,sans-serif;z-index:5}
`
  document.head.append(style)
}
