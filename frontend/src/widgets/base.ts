/** Infrastructure commune des widgets de synoptique. */

import type { AlarmsApi, HistoryApi, LiveApi, LiveSample, PointInfo, PointsApi, WriteApi } from '../api/types'
import type { WidgetDef, WidgetType } from '../synoptic/model'
import { applyStyle, compileRules, type CompiledRule, resolveStyle } from '../synoptic/style'
import type { StyleMap } from '../synoptic/model'
import type { EditorField, WidgetInstance } from './types'

export type WidgetApi = HistoryApi & AlarmsApi & Partial<PointsApi> & Partial<WriteApi>

export interface WidgetContext {
  api: WidgetApi
  live?: LiveApi
  /** Écriture d'une consigne (`null` relâche la priorité). Rejette avec l'erreur du serveur. */
  write?: (pointId: string, value: number | null, priority: number) => Promise<void>
  /** L'utilisateur peut-il commander (rôle opérateur ou plus) ? */
  canWrite: boolean
  navigate: (target: string) => void
  /** Thème du canevas : les textes par défaut restent lisibles sur son fond. */
  theme: 'light' | 'dark'
  /** Nom, unité, bornes d'écriture, libellés d'un point (mis en cache par l'appelant). */
  pointInfo?: (pointId: string) => Promise<PointInfo | null>
  /** Demande de confirmation avant une commande. */
  confirm: (message: string) => Promise<boolean>
  /** Éditeur : pas de réseau ni d'interaction, valeur d'exemple. */
  design?: boolean
}

export interface WidgetModule {
  type: WidgetType
  label: string
  /** Géométrie, liaison et style par défaut d'un nouveau widget (sans `id`). */
  defaults: () => Omit<WidgetDef, 'id'>
  editorSchema: EditorField[]
  /** Valeur montrée dans l'éditeur, où aucun point n'est interrogé. */
  designSample?: { value: number | null; status: string }
  render: (container: HTMLElement, widget: WidgetDef, ctx: WidgetContext) => WidgetInstance
}

export function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className = '',
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

export function svgElement<K extends keyof SVGElementTagNameMap>(
  tag: K,
  attributes: Record<string, string | number> = {},
): SVGElementTagNameMap[K] {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag)
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value))
  return node
}

export function boundPoint(widget: WidgetDef): string | null {
  const point = widget.bind?.point
  return typeof point === 'string' && point ? point : null
}

export function stringProp(widget: WidgetDef, key: string, fallback = ''): string {
  const value = widget[key]
  return typeof value === 'string' ? value : fallback
}

export function numberProp(widget: WidgetDef, key: string, fallback: number): number {
  const value = widget[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

const formatters = new Map<string, Intl.NumberFormat>()

/** `format` : `0`, `0.0`, `0.00`, `#,##0.0`... (nombre de décimales, séparateur de milliers). */
export function formatNumber(value: number | null, format?: unknown): string {
  if (value === null || !Number.isFinite(value)) return '—'
  const pattern = typeof format === 'string' ? format : ''
  const match = /^[#,]*0(?:\.(0+))?$/.exec(pattern)
  const decimals = match ? (match[1]?.length ?? 0) : Math.abs(value) >= 100 ? 1 : 2
  const grouping = pattern.includes(',')
  const key = `${decimals}:${grouping}:${Boolean(match)}`
  let formatter = formatters.get(key)
  if (!formatter) {
    formatter = new Intl.NumberFormat('fr-FR', {
      minimumFractionDigits: match ? decimals : 0,
      maximumFractionDigits: decimals,
      useGrouping: grouping,
    })
    formatters.set(key, formatter)
  }
  return formatter.format(value)
}

/**
 * Élément racine d'un widget : classe, style de base, règles d'affichage.
 * `refresh(vars)` réévalue les règles quand la valeur ou l'état change.
 */
export class Shell {
  readonly root: HTMLElement
  private readonly rules: CompiledRule[]
  private applied: StyleMap = {}

  constructor(container: HTMLElement, private readonly widget: WidgetDef, className: string) {
    this.root = element('div', `w ${className}`)
    this.root.dataset.widget = widget.id
    this.rules = compileRules(widget.rules)
    applyStyle(this.root, widget.style ?? {})
    this.applied = { ...(widget.style ?? {}) }
    container.append(this.root)
  }

  refresh(value: number | null, status: string): void {
    const style = resolveStyle(this.widget.style ?? {}, this.rules, { value, status })
    applyStyle(this.root, style, this.applied)
    this.applied = style
  }

  destroy(): void {
    this.root.remove()
  }
}

/** Instance sans effet, pour un widget qui n'attend aucune valeur. */
export function staticInstance(shell: Shell, extra?: () => void): WidgetInstance {
  return {
    update: () => undefined,
    destroy() {
      extra?.()
      shell.destroy()
    },
  }
}

export type { LiveSample }

const STYLE_ID = 'synoptic-widget-styles'

/** Feuille de style commune : variables `--syn-*` posées par le viewer, `--w-*` par les règles. */
export function injectWidgetStyles(): void {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
.w{box-sizing:border-box;width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:hidden;color:var(--w-color,var(--syn-ink,#fff));font:14px/1.3 system-ui,-apple-system,"Segoe UI",sans-serif}
.w-value{flex-direction:column;gap:2px}
.w-value .caption{font-size:.55em;opacity:.75}
.w-value .number{font-size:var(--w-font-size,28px);font-weight:600;white-space:nowrap}
.w-value .unit{font-size:.55em;font-weight:400;margin-left:.25em;opacity:.8}
.w-label{justify-content:var(--w-text-align-flex,flex-start);white-space:pre-wrap;font-size:var(--w-font-size,16px)}
.w-gauge svg{width:100%;height:100%}
.w-gauge .track{fill:none;stroke:var(--syn-muted,#64748b);stroke-width:8;opacity:.35;stroke-linecap:round}
.w-gauge .fill{fill:none;stroke:var(--w-color,#38bdf8);stroke-width:8;stroke-linecap:round;transition:stroke-dasharray .3s}
.w-gauge text{fill:var(--syn-ink,#fff);font-family:inherit}
.w-gauge .val{font-size:14px;font-weight:600;text-anchor:middle}
.w-gauge .bound{font-size:6px;opacity:.7}
.w-indicator{gap:8px;justify-content:flex-start;padding:0 4px}
.w-indicator .lamp{flex:none;width:20px;height:20px;border-radius:50%;background:var(--lamp,#64748b);box-shadow:0 0 0 2px var(--syn-canvas,#0f172a),0 0 0 3px var(--syn-muted,#64748b)}
.w-indicator .text{white-space:nowrap}
.w-switch{gap:10px;justify-content:flex-start;padding:0 4px}
.w-switch button{display:flex;align-items:center;gap:10px;padding:0;border:0;background:none;color:inherit;font:inherit;cursor:pointer}
.w-switch button:disabled{cursor:not-allowed;opacity:.6}
.w-switch .track{position:relative;flex:none;width:44px;height:24px;border-radius:12px;background:var(--syn-muted,#64748b);transition:background .15s}
.w-switch .knob{position:absolute;left:3px;top:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:left .15s}
.w-switch[data-state="on"] .track{background:var(--w-color,#22c55e)}
.w-switch[data-state="on"] .knob{left:23px}
.w-switch .error{color:#f87171;font-size:12px}
.w-switch button:focus-visible,.w-setpoint input:focus-visible,.w-setpoint button:focus-visible,.w-link button:focus-visible{outline:2px solid var(--syn-ink,#fff);outline-offset:2px}
.w-setpoint{flex-direction:column;align-items:stretch;gap:4px;padding:2px 4px}
.w-setpoint .row{display:flex;gap:6px;align-items:center}
.w-setpoint .caption{font-size:12px;opacity:.75}
.w-setpoint input{flex:1;min-width:0;padding:4px 6px;border:1px solid var(--syn-muted,#64748b);border-radius:6px;background:transparent;color:inherit;font:inherit}
.w-setpoint button,.w-link button{padding:4px 10px;border:1px solid var(--syn-muted,#64748b);border-radius:6px;background:transparent;color:inherit;font:inherit;cursor:pointer}
.w-setpoint button:hover:not(:disabled),.w-link button:hover{background:rgba(148,163,184,.2)}
.w-setpoint button:disabled,.w-setpoint input:disabled{opacity:.6;cursor:not-allowed}
.w-setpoint .msg{font-size:12px;min-height:1em}
.w-setpoint .msg.error{color:#f87171}
.w-image img{max-width:100%;max-height:100%;width:100%;height:100%;object-fit:var(--fit,contain)}
.w-link button{width:100%;height:100%;font-size:var(--w-font-size,16px);background:var(--w-background,transparent);border-radius:var(--w-border-radius,6px)}
.w-shape svg{width:100%;height:100%;overflow:visible}
.w-shape .shape{fill:var(--w-fill,none);stroke:var(--w-stroke,#94a3b8);stroke-width:var(--w-stroke-width,2)}
.w-shape .pipe{fill:none;stroke:var(--w-stroke,#38bdf8);stroke-width:var(--w-stroke-width,10);stroke-linecap:round}
.w-shape .flow{fill:none;stroke:#fff;stroke-opacity:.55;stroke-width:3;stroke-dasharray:4 12;stroke-linecap:round}
.w-shape[data-flow="on"] .flow{animation:syn-flow 1s linear infinite}
@keyframes syn-flow{to{stroke-dashoffset:-16}}
@media (prefers-reduced-motion:reduce){.w-shape[data-flow="on"] .flow{animation:none}}
.w-placeholder{flex-direction:column;gap:4px;border:1px dashed var(--syn-muted,#64748b);border-radius:6px;color:var(--syn-muted,#94a3b8);text-align:center;padding:8px}
`
  document.head.append(style)
}
