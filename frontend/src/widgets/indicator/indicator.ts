import type { LiveSample } from '../../api/types'
import type { WidgetDef } from '../../synoptic/model'
import { boundPoint, element, injectWidgetStyles, Shell, type WidgetContext, type WidgetModule } from '../base'
import type { WidgetInstance } from '../types'

export interface IndicatorState {
  value: number
  label: string
  color: string
}

export const BINARY_STATES: IndicatorState[] = [
  { value: 0, label: 'Arrêt', color: '#64748b' },
  { value: 1, label: 'Marche', color: '#22c55e' },
]

const MULTI_COLORS = ['#3987e5', '#eda100', '#1baf7a', '#e87ba4', '#9085e9', '#e66767', '#d95926', '#199e70']

function statesOf(widget: WidgetDef): IndicatorState[] | null {
  const raw = widget.states
  if (!Array.isArray(raw) || raw.length === 0) return null
  return raw
    .filter((s): s is IndicatorState => typeof s?.value === 'number' && typeof s?.color === 'string')
    .map((s) => ({ value: s.value, label: String(s.label ?? ''), color: s.color }))
}

/** Voyant binaire ou multi-états : la couleur est toujours doublée d'un libellé. */
function render(container: HTMLElement, widget: WidgetDef, ctx: WidgetContext): WidgetInstance {
  injectWidgetStyles()
  const shell = new Shell(container, widget, 'w-indicator')
  const lamp = element('span', 'lamp')
  lamp.setAttribute('aria-hidden', 'true')
  const text = element('span', 'text', '—')
  shell.root.append(lamp, text)
  shell.root.setAttribute('role', 'status')

  const point = boundPoint(widget)
  const configured = statesOf(widget)
  let stateText: string[] | null = null
  let destroyed = false
  let last: LiveSample | null = null
  const showLabel = widget.showLabel !== false
  text.hidden = !showLabel

  const describe = (value: number | null): { label: string; color: string } => {
    if (value === null) return { label: 'Inconnu', color: '#64748b' }
    const found = configured?.find((s) => s.value === value)
    if (found) return { label: found.label, color: found.color }
    if (!configured && stateText) {
      // Multi-état : les libellés du point (index 1 = premier libellé) et une palette fixe.
      return { label: stateText[value - 1] ?? `État ${value}`, color: MULTI_COLORS[(value - 1) % MULTI_COLORS.length] as string }
    }
    if (!configured) {
      const binary = BINARY_STATES.find((s) => s.value === value)
      if (binary) return binary
    }
    return { label: `État ${value}`, color: '#64748b' }
  }
  const show = (value: number | null, status: string) => {
    const { label, color } = describe(value)
    lamp.style.setProperty('--lamp', color)
    text.textContent = label
    shell.root.setAttribute('aria-label', label)
    shell.root.dataset.status = status
    shell.refresh(value, status)
  }
  if (point && !configured && ctx.pointInfo) {
    void ctx.pointInfo(point).then((info) => {
      if (destroyed || !info?.state_text?.length) return
      stateText = info.state_text
      if (last) show(last.status === 'comm_lost' ? null : last.value, last.status)
    })
  }
  show(null, point ? 'unknown' : 'unbound')
  return {
    update(sample: LiveSample) {
      last = sample
      show(sample.status === 'comm_lost' ? null : sample.value, sample.status)
    },
    destroy() {
      destroyed = true
      shell.destroy()
    },
  }
}

export const indicatorModule: WidgetModule = {
  type: 'indicator',
  label: 'Voyant',
  designSample: { value: 1, status: 'ok' },
  defaults: () => ({ x: 0, y: 0, w: 140, h: 32, bind: {}, style: {}, rules: [], showLabel: true }),
  editorSchema: [
    { key: 'bind.point', label: 'Point', type: 'point' },
    { key: 'showLabel', label: "Afficher l'état en texte", type: 'boolean', default: true },
  ],
  render,
}
