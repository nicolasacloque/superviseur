import { ApiError } from '../../api/client'
import type { Alarm, AlarmEvent, AlarmsApi, LiveApi, LiveState } from '../../api/types'
import { resolveTheme, type Theme } from '../theme'
import type { WidgetInstance } from '../types'
import {
  type AlarmFilter,
  applyAlarm,
  describeCondition,
  formatSince,
  needsAck,
  SEVERITY_LABELS,
  sortAlarms,
  STATE_LABELS,
} from './alarms'
import { type AlarmListConfig, normalizeConfig } from './schema'

/** Les âges (« il y a 5 min ») sont rafraîchis à cette période. */
export const AGE_REFRESH_MS = 30_000

export interface AlarmListDeps {
  api: AlarmsApi
  /** Sans `live`, la liste ne se met à jour qu'avec `refresh()`. */
  live?: LiveApi
  now?: () => number
  /** Exécute `fn` tous les `periodMs` ; retourne l'annulation. */
  every?: (fn: () => void, periodMs: number) => () => void
  prefersDark?: () => boolean
}

export interface AlarmListInstance extends WidgetInstance {
  /** Résolu quand la première liste est affichée. */
  ready: Promise<void>
  refresh(): Promise<void>
  /** Applique une transition d'alarme reçue en temps réel. */
  applyEvent(event: AlarmEvent): void
}

/** Liste des alarmes filtrée par chemin, avec acquittement et mise à jour en direct. */
export function render(
  container: HTMLElement,
  rawConfig: unknown,
  deps: AlarmListDeps,
): AlarmListInstance {
  const config: AlarmListConfig = normalizeConfig(rawConfig)
  const now = deps.now ?? (() => Date.now())
  const every = deps.every ?? defaultEvery
  const theme = resolveTheme(
    config.theme,
    (deps.prefersDark ?? defaultPrefersDark)(),
    document.documentElement.dataset.theme,
  )
  const filter: AlarmFilter = { path: config.path, states: config.states }

  let alarms: Alarm[] = []
  let loaded = false
  let destroyed = false
  let loadSeq = 0
  let controller: AbortController | null = null
  let liveState: LiveState | null = deps.live?.state ?? null
  let wasClosed = false
  const acking = new Set<string>()
  const messages = { load: '', ack: '', live: '' }

  // -- DOM ---------------------------------------------------------------------------------
  injectStyles()
  const root = element('section', 'alarms')
  root.setAttribute('aria-label', config.path ? `Alarmes : ${config.path}` : 'Alarmes')
  applyTheme(root, theme)
  const summary = element('div', 'alarms__summary')
  const message = element('div', 'alarms__message')
  message.setAttribute('role', 'status')
  message.hidden = true
  const scroller = element('div', 'alarms__scroll')
  root.append(summary, message, scroller)
  container.append(root)

  function showMessage(): void {
    const text = messages.live || messages.ack || messages.load
    message.textContent = text
    message.hidden = text === ''
    root.dataset.state = messages.live ? 'stale' : loaded ? 'ok' : 'loading'
  }

  function renderAll(): void {
    const waiting = alarms.filter(needsAck).length
    summary.textContent = !loaded
      ? 'Chargement des alarmes…'
      : alarms.length === 0
        ? 'Aucune alarme'
        : `${alarms.length} alarme${alarms.length > 1 ? 's' : ''}` +
          (waiting ? ` dont ${waiting} à acquitter` : '')
    summary.dataset.waiting = String(waiting)
    if (!loaded) {
      scroller.replaceChildren()
      return
    }
    if (alarms.length === 0) {
      scroller.replaceChildren(element('p', 'alarms__empty', 'Tout est normal : aucune alarme à afficher.'))
      return
    }
    const table = element('table')
    table.append(caption(), head(), body())
    scroller.replaceChildren(table)
  }

  function caption(): HTMLElement {
    return element('caption', 'sr-only', 'Liste des alarmes, à acquitter en premier')
  }

  function head(): HTMLElement {
    const row = element('tr')
    const labels = ['Sévérité', 'Point', 'Condition', 'État', 'Depuis']
    if (config.allowAck) labels.push('Action')
    for (const label of labels) {
      const th = element('th', '', label)
      th.scope = 'col'
      row.append(th)
    }
    const thead = element('thead')
    thead.append(row)
    return thead
  }

  function body(): HTMLElement {
    const tbody = element('tbody')
    for (const alarm of alarms) {
      const row = element('tr', needsAck(alarm) ? 'is-waiting' : '')
      row.dataset.id = alarm.id
      row.dataset.severity = alarm.severity

      const severity = element('td', 'severity')
      const marker = element('span', `marker marker--${alarm.severity}`)
      marker.setAttribute('aria-hidden', 'true')
      severity.append(marker, element('span', '', SEVERITY_LABELS[alarm.severity]))

      // Nom de point, de règle et chemin viennent de l'API : toujours insérés en texte.
      const point = element('td', 'point')
      point.append(element('div', 'name', alarm.rule_name || alarm.point_name))
      point.append(element('div', 'path', alarm.path || alarm.point_name))

      const state = element('td', 'state', STATE_LABELS[alarm.state])
      if (alarm.acked_by) state.append(element('div', 'by', `par ${alarm.acked_by}`))

      const since = element('td', 'since', formatSince(alarm.raised_at, now()))
      since.title = new Date(alarm.raised_at).toLocaleString('fr-FR')

      row.append(severity, point, element('td', 'condition', describeCondition(alarm)), state, since)
      if (config.allowAck) row.append(actionCell(alarm))
      tbody.append(row)
    }
    return tbody
  }

  function actionCell(alarm: Alarm): HTMLElement {
    const cell = element('td', 'action')
    if (!needsAck(alarm)) return cell
    const button = element('button', '', acking.has(alarm.id) ? 'Envoi…' : 'Acquitter')
    button.type = 'button'
    button.disabled = acking.has(alarm.id)
    button.setAttribute('aria-label', `Acquitter l'alarme ${alarm.rule_name || alarm.point_name}`)
    button.addEventListener('click', () => void acknowledge(alarm.id))
    cell.append(button)
    return cell
  }

  // -- Données -----------------------------------------------------------------------------
  async function refresh(): Promise<void> {
    const seq = ++loadSeq
    controller?.abort()
    const current = (controller = new AbortController())
    try {
      const page = await deps.api.alarms(
        { state: config.states, path: config.path, limit: config.maxRows },
        current.signal,
      )
      if (seq !== loadSeq || destroyed) return
      alarms = sortAlarms(page.items).slice(0, config.maxRows)
      messages.load = ''
      loaded = true
    } catch (error) {
      if (seq !== loadSeq || destroyed) return
      messages.load = `Alarmes indisponibles : ${errorText(error)}`
      loaded = true
    }
    showMessage()
    renderAll()
  }

  async function acknowledge(alarmId: string): Promise<void> {
    if (acking.has(alarmId) || destroyed) return
    acking.add(alarmId)
    messages.ack = ''
    renderAll()
    try {
      const updated = await deps.api.acknowledge(alarmId)
      if (destroyed) return
      alarms = applyAlarm(alarms, updated, filter, config.maxRows)
    } catch (error) {
      if (destroyed) return
      messages.ack =
        error instanceof ApiError && error.status === 403
          ? "Droits insuffisants pour acquitter cette alarme"
          : `Acquittement impossible : ${errorText(error)}`
      // L'alarme a pu changer entre-temps (déjà acquittée, terminée) : on resynchronise.
      void refresh()
    } finally {
      acking.delete(alarmId)
    }
    showMessage()
    renderAll()
  }

  function applyEvent(event: AlarmEvent): void {
    if (destroyed || !loaded) return
    alarms = applyAlarm(alarms, event.event, filter, config.maxRows)
    renderAll()
  }

  // -- Temps réel --------------------------------------------------------------------------
  const cleanups: (() => void)[] = []
  if (deps.live) {
    cleanups.push(deps.live.subscribeAlarms(applyEvent))
    cleanups.push(
      deps.live.onState((state) => {
        liveState = state
        messages.live = state === 'closed' ? 'Connexion perdue : la liste peut être en retard' : ''
        showMessage()
        if (state === 'closed') wasClosed = true
        else if (state === 'open' && wasClosed) {
          wasClosed = false
          void refresh() // rattrape ce qui s'est passé pendant la coupure
        }
      }),
    )
  }
  cleanups.push(every(() => renderAll(), AGE_REFRESH_MS))
  if (liveState === 'closed') messages.live = ''

  showMessage()
  renderAll()
  const ready = refresh()

  return {
    ready,
    refresh,
    applyEvent,
    update: () => undefined, // pas de valeurs de points : les alarmes arrivent par applyEvent
    destroy() {
      destroyed = true
      controller?.abort()
      for (const cleanup of cleanups) cleanup()
      root.remove()
    },
  }
}

// -- Utilitaires -------------------------------------------------------------------------------

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

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function defaultEvery(fn: () => void, periodMs: number): () => void {
  const handle = setInterval(fn, periodMs)
  return () => clearInterval(handle)
}

function defaultPrefersDark(): boolean {
  return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches
}

function applyTheme(node: HTMLElement, theme: Theme): void {
  node.style.setProperty('--al-surface', theme.surface)
  node.style.setProperty('--al-ink', theme.ink)
  node.style.setProperty('--al-ink-secondary', theme.inkSecondary)
  node.style.setProperty('--al-muted', theme.muted)
  node.style.setProperty('--al-grid', theme.grid)
  node.style.setProperty('--al-axis', theme.axis)
}

const STYLE_ID = 'alarm-list-widget-styles'

function injectStyles(): void {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  // Couleurs d'état fixes (jamais thématisées) : critique #d03b3b, avertissement #fab219. Elles
  // ne portent jamais seules le sens : chaque ligne a aussi une forme et un libellé.
  style.textContent = `
.alarms{position:relative;display:flex;flex-direction:column;gap:8px;box-sizing:border-box;width:100%;height:100%;min-height:160px;padding:12px;border-radius:8px;background:var(--al-surface);color:var(--al-ink);font:13px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
.alarms__summary{font-weight:600}
.alarms__summary[data-waiting]:not([data-waiting="0"]){color:var(--al-ink)}
.alarms__message{padding:4px 10px;border-radius:6px;background:var(--al-grid);font-size:12px}
.alarms[data-state="stale"] .alarms__scroll{opacity:.6}
.alarms__scroll{flex:1;overflow:auto}
.alarms__empty{margin:16px 0;color:var(--al-ink-secondary)}
.alarms table{width:100%;border-collapse:collapse}
.alarms th,.alarms td{padding:6px 8px;text-align:left;vertical-align:top;border-bottom:1px solid var(--al-grid)}
.alarms th{position:sticky;top:0;background:var(--al-surface);color:var(--al-ink-secondary);font-weight:600;white-space:nowrap}
.alarms tr.is-waiting td{font-weight:600}
.alarms .name{color:var(--al-ink)}
.alarms .path,.alarms .by,.alarms .since,.alarms .condition{color:var(--al-ink-secondary);font-weight:400}
.alarms .path{font-size:12px;font-weight:400}
.alarms td.severity{white-space:nowrap}
.alarms .marker{display:inline-block;width:10px;height:10px;margin-right:6px;vertical-align:baseline}
.alarms .marker--critical{background:#d03b3b;transform:rotate(45deg) scale(.85)}
.alarms .marker--warning{width:0;height:0;border-left:6px solid transparent;border-right:6px solid transparent;border-bottom:11px solid #fab219}
.alarms .marker--info{border-radius:50%;background:var(--al-muted)}
.alarms button{font:inherit;color:var(--al-ink);background:transparent;border:1px solid var(--al-axis);border-radius:6px;padding:3px 10px;cursor:pointer}
.alarms button:hover:not(:disabled){background:var(--al-grid)}
.alarms button:disabled{opacity:.6;cursor:default}
.alarms button:focus-visible{outline:2px solid var(--al-ink-secondary);outline-offset:1px}
.alarms .sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
@media (forced-colors:active){.alarms .marker{forced-color-adjust:none;border-color:CanvasText}}
`
  document.head.append(style)
}
