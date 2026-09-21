import type { HistoryApi, LiveApi, LiveSample, PointInfo } from '../../api/types'
import type { WidgetInstance } from '../types'
import type { PlotAdapter, PlotFactory } from './plot'
import { normalizeConfig, type TrendConfig } from './schema'
import {
  type Aligned,
  alignSeries,
  appendLive,
  emptySeries,
  formatValue,
  historyToSeries,
  lastPoint,
  maxPointsFor,
  type Range,
  RANGE_SECONDS,
  RANGES,
  type SeriesData,
  trimBefore,
} from './series'
import { resolveTheme, type Theme } from './theme'

/** Au plus 10 rendus par seconde et par widget (section 10.5). */
export const MIN_DRAW_INTERVAL_MS = 100
/** Nombre de points temps réel conservés par fenêtre : borne la mémoire sur 7 ou 30 jours. */
export const LIVE_POINTS_PER_WINDOW = 1000
/** La fenêtre glisse même sans nouvelle valeur. */
export const WINDOW_TICK_MS = 5000
const TABLE_ROWS = 50
const DEFAULT_SIZE = { width: 640, height: 260 }

export interface TrendDeps {
  api: HistoryApi
  /** Sans `live`, la courbe reste figée sur l'historique. */
  live?: LiveApi
  /** Moteur de tracé ; uPlot par défaut, chargé à la demande. */
  createPlot?: PlotFactory
  /** Horloge en millisecondes. */
  now?: () => number
  /** Exécute `fn` après `delayMs` ; retourne l'annulation. */
  schedule?: (fn: () => void, delayMs: number) => () => void
  prefersDark?: () => boolean
}

export interface TrendInstance extends WidgetInstance {
  /** Résolu quand l'historique est chargé et le premier tracé fait. */
  ready: Promise<void>
  setRange(range: Range): Promise<void>
}

const RANGE_LABELS: Record<Range, string> = {
  '15m': '15 min',
  '1h': '1 h',
  '6h': '6 h',
  '24h': '24 h',
  '7d': '7 j',
  '30d': '30 j',
}

/** Affiche `config.bind.points` (8 au plus) : historique agrégé puis valeurs en direct. */
export function render(container: HTMLElement, rawConfig: unknown, deps: TrendDeps): TrendInstance {
  const config: TrendConfig = normalizeConfig(rawConfig)
  const now = deps.now ?? (() => Date.now())
  const schedule = deps.schedule ?? defaultSchedule
  const theme = resolveTheme(
    config.theme,
    (deps.prefersDark ?? defaultPrefersDark)(),
    document.documentElement.dataset.theme,
  )
  const points = config.bind.points
  const labels = points.map((p) => p.label ?? p.point.slice(0, 8))
  const units: (string | null)[] = points.map(() => null)
  let series: SeriesData[] = points.map(() => emptySeries())
  let aligned: Aligned = alignSeries(series)

  let range = config.range
  let loaded = false
  let destroyed = false
  let loadSeq = 0
  let controller: AbortController | null = null
  let plot: PlotAdapter | null = null
  const pending: LiveSample[] = []
  const messages = { history: '', live: '' }

  // -- DOM ---------------------------------------------------------------------------------
  injectStyles()
  const root = element('div', 'trend')
  root.setAttribute('role', 'group')
  root.setAttribute('aria-label', 'Courbe de tendance')
  root.dataset.state = 'loading'
  applyTheme(root, theme)

  const bar = element('div', 'trend__bar')
  const rangeButtons = new Map<Range, HTMLButtonElement>()
  if (config.rangeSelector) {
    for (const value of RANGES) {
      const button = element('button', '', RANGE_LABELS[value])
      button.type = 'button'
      button.addEventListener('click', () => void setRange(value))
      rangeButtons.set(value, button)
      bar.append(button)
    }
  }
  bar.append(element('span', 'spacer'))
  const tableToggle = element('button', '', 'Tableau')
  tableToggle.type = 'button'
  tableToggle.setAttribute('aria-pressed', 'false')
  bar.append(tableToggle)

  const stage = element('div', 'trend__stage')
  const plotHost = element('div', 'trend__plot')
  const message = element('div', 'trend__message')
  message.setAttribute('role', 'status')
  message.hidden = true
  const tooltip = element('div', 'trend__tooltip')
  tooltip.hidden = true
  stage.append(plotHost, message, tooltip)

  const legend = element('ul', 'trend__legend')
  const legendValues: HTMLElement[] = []
  const legendNames: HTMLElement[] = []
  points.forEach((_, index) => {
    const item = element('li')
    const key = element('span', 'key')
    key.style.background = seriesColor(index)
    const name = element('span', 'name', labels[index])
    const last = element('span', 'last', '—')
    legendNames.push(name)
    legendValues.push(last)
    item.append(key, name, last)
    legend.append(item)
  })

  const tableWrap = element('div', 'trend__table')
  tableWrap.hidden = true
  root.append(bar, stage, legend, tableWrap)
  container.append(root)
  syncRangeButtons()

  tableToggle.addEventListener('click', () => {
    const open = tableWrap.hidden
    tableWrap.hidden = !open
    tableToggle.setAttribute('aria-pressed', String(open))
    if (open) renderTable()
  })

  function seriesColor(index: number): string {
    return theme.series[index % theme.series.length] ?? theme.ink
  }

  function syncRangeButtons(): void {
    for (const [value, button] of rangeButtons) {
      button.setAttribute('aria-pressed', String(value === range))
    }
  }

  function showMessage(): void {
    const text = messages.live || messages.history
    message.textContent = text
    message.hidden = text === ''
    root.dataset.state = messages.live ? 'stale' : loaded ? 'ok' : 'loading'
  }

  // -- Rendu -------------------------------------------------------------------------------
  let drawCancel: (() => void) | null = null
  let lastDraw = -Infinity

  function requestDraw(): void {
    if (drawCancel || destroyed) return
    const wait = Math.max(0, MIN_DRAW_INTERVAL_MS - (now() - lastDraw))
    drawCancel = schedule(() => {
      drawCancel = null
      lastDraw = now()
      draw()
    }, wait)
  }

  function draw(): void {
    if (destroyed) return
    const nowSeconds = now() / 1000
    const from = nowSeconds - RANGE_SECONDS[range]
    for (const s of series) trimBefore(s, from)
    aligned = alignSeries(series)
    plot?.setData(aligned)
    plot?.setXRange(from, nowSeconds)
    series.forEach((s, index) => {
      const value = legendValues[index]
      if (value) value.textContent = formatValue(lastPoint(s)?.v ?? null, units[index])
    })
    if (!tableWrap.hidden) renderTable()
  }

  function refreshLabels(): void {
    labels.forEach((label, index) => {
      const name = legendNames[index]
      if (name) name.textContent = label
    })
    root.setAttribute('aria-label', `Courbe de tendance : ${labels.join(', ')}`)
  }

  function renderTable(): void {
    const [times, ...columns] = aligned
    const table = element('table')
    const head = element('tr')
    head.append(element('th', '', 'Heure'), ...labels.map((label) => element('th', '', label)))
    table.append(head)
    const start = Math.max(0, times.length - TABLE_ROWS)
    for (let row = times.length - 1; row >= start; row--) {
      const tr = element('tr')
      tr.append(element('td', '', formatTime(times[row] ?? 0, range)))
      columns.forEach((column, index) =>
        tr.append(element('td', 'num', formatValue(column[row] ?? null, units[index]))),
      )
      table.append(tr)
    }
    tableWrap.replaceChildren(table)
  }

  function showTooltip(index: number | null, left: number, top: number): void {
    if (index === null || left < 0) {
      tooltip.hidden = true
      return
    }
    const time = aligned[0][index]
    if (time === undefined) {
      tooltip.hidden = true
      return
    }
    const rows = points.map((_, i) => {
      const row = element('div', 'row')
      const key = element('span', 'key')
      key.style.background = seriesColor(i)
      // Les valeurs mènent, le nom de la série suit ; libellés insérés en textContent.
      row.append(
        key,
        element('span', 'value', formatValue(aligned[i + 1]?.[index] ?? null, units[i])),
        element('span', 'label', labels[i] ?? ''),
      )
      return row
    })
    tooltip.replaceChildren(element('div', 'time', formatTime(time, range)), ...rows)
    tooltip.hidden = false
    const width = tooltip.offsetWidth
    const room = stage.clientWidth
    tooltip.style.left = `${room && left + 16 + width > room ? Math.max(0, left - 16 - width) : left + 16}px`
    tooltip.style.top = `${Math.max(0, top - 8)}px`
  }

  async function ensurePlot(): Promise<void> {
    if (plot || destroyed) return
    const factory = deps.createPlot ?? (await import('./uplot-plot')).uplotFactory
    if (destroyed || plot) return
    const size = {
      width: plotHost.clientWidth || DEFAULT_SIZE.width,
      height: plotHost.clientHeight || DEFAULT_SIZE.height,
    }
    const from = now() / 1000 - RANGE_SECONDS[range]
    plot = factory(plotHost, {
      ...size,
      series: labels.map((label, index) => ({ label, color: seriesColor(index) })),
      theme,
      yMin: config.yMin,
      yMax: config.yMax,
      data: aligned,
      xRange: [from, now() / 1000],
    })
    plot.onCursor(showTooltip)
    if (typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(() => {
        plot?.setSize(plotHost.clientWidth || DEFAULT_SIZE.width, plotHost.clientHeight || DEFAULT_SIZE.height)
      })
      resizeObserver.observe(stage)
    }
  }
  let resizeObserver: ResizeObserver | null = null

  // -- Historique --------------------------------------------------------------------------
  async function loadHistory(): Promise<void> {
    const seq = ++loadSeq
    controller?.abort()
    const current = (controller = new AbortController())
    loaded = false
    root.classList.add('is-refetching') // on garde le cadre précédent, estompé, pendant le rechargement
    const to = new Date(now())
    const from = new Date(to.getTime() - RANGE_SECONDS[range] * 1000)
    const maxPoints = maxPointsFor(plotHost.clientWidth || DEFAULT_SIZE.width)
    const results = await Promise.allSettled(
      points.map((p) => deps.api.history(p.point, { from, to, bucket: 'auto', maxPoints }, current.signal)),
    )
    if (seq !== loadSeq || destroyed) return
    series = results.map((r) => (r.status === 'fulfilled' ? historyToSeries(r.value.items) : emptySeries()))
    const failed = results.filter((r) => r.status === 'rejected').length
    messages.history = failed ? `Historique indisponible pour ${failed} courbe${failed > 1 ? 's' : ''}` : ''
    loaded = true
    for (const sample of pending.splice(0)) applyLive(sample)
    await ensurePlot()
    root.classList.remove('is-refetching')
    showMessage()
    draw()
  }

  async function loadPointInfo(): Promise<void> {
    if (!deps.api.point) return
    const infos = await Promise.allSettled(points.map((p) => deps.api.point?.(p.point) as Promise<PointInfo>))
    if (destroyed) return
    infos.forEach((result, index) => {
      if (result.status !== 'fulfilled') return
      units[index] = result.value.unit
      if (!points[index]?.label) labels[index] = result.value.name
    })
    refreshLabels()
    draw()
  }

  async function setRange(next: Range): Promise<void> {
    if (next === range && loaded) return
    range = next
    syncRangeButtons()
    await loadHistory()
  }

  // -- Temps réel --------------------------------------------------------------------------
  function applyLive(sample: LiveSample): boolean {
    const index = points.findIndex((p) => p.point === sample.point)
    const target = series[index]
    if (!target) return false
    // Une valeur perdue (comm_lost) devient un trou dans la courbe, pas un zéro.
    const value = sample.status === 'comm_lost' ? null : sample.value
    appendLive(target, sample.ts, value, RANGE_SECONDS[range] / LIVE_POINTS_PER_WINDOW)
    return true
  }

  function update(sample: LiveSample): void {
    if (destroyed) return
    if (!loaded) {
      pending.push(sample)
      return
    }
    if (applyLive(sample)) requestDraw() // une valeur d'un autre point ne redessine rien
  }

  const unsubscribers: (() => void)[] = []
  if (config.live && deps.live) {
    const live = deps.live
    unsubscribers.push(live.subscribe(points.map((p) => p.point), update))
    unsubscribers.push(
      live.onState((state) => {
        messages.live = state === 'closed' ? 'Connexion perdue : les valeurs ne sont plus à jour' : ''
        showMessage()
      }),
    )
  }

  let tickCancel: (() => void) | null = null
  const tick = (): void => {
    tickCancel = schedule(() => {
      requestDraw()
      tick()
    }, WINDOW_TICK_MS)
  }
  if (config.live) tick()

  const ready = Promise.all([loadHistory(), loadPointInfo()]).then(() => undefined)

  return {
    ready,
    setRange,
    update,
    destroy() {
      destroyed = true
      controller?.abort()
      drawCancel?.()
      tickCancel?.()
      for (const unsubscribe of unsubscribers) unsubscribe()
      resizeObserver?.disconnect()
      plot?.destroy()
      root.remove()
    },
  }
}

// -- Utilitaires DOM ---------------------------------------------------------------------------

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className = '',
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

function formatTime(seconds: number, range: Range): string {
  const date = new Date(seconds * 1000)
  return RANGE_SECONDS[range] > 86400
    ? date.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
    : date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function defaultSchedule(fn: () => void, delayMs: number): () => void {
  let frame = 0
  const timer = setTimeout(() => {
    if (typeof requestAnimationFrame === 'function') frame = requestAnimationFrame(fn)
    else fn()
  }, delayMs)
  return () => {
    clearTimeout(timer)
    if (frame && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame)
  }
}

function defaultPrefersDark(): boolean {
  return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches
}

function applyTheme(node: HTMLElement, theme: Theme): void {
  node.style.setProperty('--trend-surface', theme.surface)
  node.style.setProperty('--trend-ink', theme.ink)
  node.style.setProperty('--trend-ink-secondary', theme.inkSecondary)
  node.style.setProperty('--trend-muted', theme.muted)
  node.style.setProperty('--trend-grid', theme.grid)
  node.style.setProperty('--trend-axis', theme.axis)
}

const STYLE_ID = 'trend-widget-styles'

function injectStyles(): void {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
.trend{position:relative;display:flex;flex-direction:column;gap:8px;box-sizing:border-box;width:100%;height:100%;min-height:240px;padding:12px;border-radius:8px;background:var(--trend-surface);color:var(--trend-ink);font:13px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
.trend__bar{display:flex;flex-wrap:wrap;align-items:center;gap:4px}
.trend__bar .spacer{flex:1}
.trend__bar button{font:inherit;color:var(--trend-ink-secondary);background:transparent;border:1px solid transparent;border-radius:6px;padding:3px 8px;cursor:pointer}
.trend__bar button:hover{background:var(--trend-grid)}
.trend__bar button[aria-pressed="true"]{color:var(--trend-ink);border-color:var(--trend-axis);font-weight:600}
.trend__bar button:focus-visible{outline:2px solid var(--trend-ink-secondary);outline-offset:1px}
.trend__stage{position:relative;flex:1;min-height:140px}
.trend__plot{position:absolute;inset:0;transition:opacity .15s}
.trend.is-refetching .trend__plot{opacity:.6}
.trend[data-state="stale"] .trend__plot{opacity:.45}
.trend__message{position:absolute;left:50%;top:8px;transform:translateX(-50%);padding:4px 10px;border-radius:6px;background:var(--trend-grid);color:var(--trend-ink);font-size:12px;white-space:nowrap}
.trend .u-hz .u-cursor-x{border-right:1px solid var(--trend-axis)}
.trend__tooltip{position:absolute;z-index:2;min-width:150px;padding:6px 8px;border:1px solid var(--trend-axis);border-radius:6px;background:var(--trend-surface);pointer-events:none}
.trend__tooltip .time{margin-bottom:4px;color:var(--trend-muted)}
.trend__tooltip .row{display:flex;align-items:center;gap:8px}
.trend__tooltip .key{flex:none;width:12px;height:2px;border-radius:1px}
.trend__tooltip .value{font-weight:600;color:var(--trend-ink)}
.trend__tooltip .label{color:var(--trend-ink-secondary)}
.trend__legend{display:flex;flex-wrap:wrap;gap:4px 16px;margin:0;padding:0;list-style:none}
.trend__legend .key{display:inline-block;width:16px;height:2px;margin-right:6px;border-radius:1px;vertical-align:middle}
.trend__legend .name{color:var(--trend-ink-secondary)}
.trend__legend .last{margin-left:6px;font-weight:600;color:var(--trend-ink)}
.trend__table{max-height:180px;overflow:auto}
.trend__table table{width:100%;border-collapse:collapse}
.trend__table th,.trend__table td{padding:2px 8px;text-align:left;border-bottom:1px solid var(--trend-grid)}
.trend__table th{position:sticky;top:0;background:var(--trend-surface);color:var(--trend-ink-secondary);font-weight:600}
.trend__table td.num{font-variant-numeric:tabular-nums;text-align:right}
@media (forced-colors:active){.trend__legend .key,.trend__tooltip .key{height:3px;background:CanvasText!important}}
`
  document.head.append(style)
}
