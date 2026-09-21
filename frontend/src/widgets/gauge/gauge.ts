import type { LiveSample } from '../../api/types'
import type { WidgetDef } from '../../synoptic/model'
import {
  boundPoint,
  formatNumber,
  injectWidgetStyles,
  numberProp,
  Shell,
  svgElement,
  type WidgetContext,
  type WidgetModule,
} from '../base'
import type { WidgetInstance } from '../types'

const ARC = 'M10 52 A40 40 0 0 1 90 52'

/** Jauge en arc entre `min` et `max`. */
function render(container: HTMLElement, widget: WidgetDef, ctx: WidgetContext): WidgetInstance {
  injectWidgetStyles()
  const shell = new Shell(container, widget, 'w-gauge')
  const min = numberProp(widget, 'min', 0)
  const max = numberProp(widget, 'max', 100)
  const svg = svgElement('svg', { viewBox: '0 0 100 62', role: 'img' })
  const track = svgElement('path', { class: 'track', d: ARC, pathLength: 100 })
  const fill = svgElement('path', { class: 'fill', d: ARC, pathLength: 100, 'stroke-dasharray': '0 100' })
  const text = svgElement('text', { class: 'val', x: 50, y: 50 })
  const low = svgElement('text', { class: 'bound', x: 10, y: 60, 'text-anchor': 'middle' })
  const high = svgElement('text', { class: 'bound', x: 90, y: 60, 'text-anchor': 'middle' })
  low.textContent = formatNumber(min, '0.#')
  high.textContent = formatNumber(max, '0.#')
  svg.append(track, fill, text, low, high)
  shell.root.append(svg)

  const point = boundPoint(widget)
  let unit = ''
  let destroyed = false
  let last: { value: number | null; status: string } = { value: null, status: point ? 'unknown' : 'unbound' }
  const show = (value: number | null, status: string) => {
    last = { value, status }
    const span = max - min
    const share = value === null || span <= 0 ? 0 : Math.min(1, Math.max(0, (value - min) / span))
    fill.setAttribute('stroke-dasharray', `${(share * 100).toFixed(1)} 100`)
    text.textContent = value === null ? '—' : `${formatNumber(value, widget.bind?.format)}${unit ? ` ${unit}` : ''}`
    svg.setAttribute('aria-label', `Jauge : ${text.textContent} (de ${min} à ${max})`)
    shell.root.dataset.status = status
    shell.refresh(value, status)
  }
  if (point && widget.bind?.unit !== false && ctx.pointInfo) {
    void ctx.pointInfo(point).then((info) => {
      if (destroyed || !info?.unit) return
      unit = info.unit
      show(last.value, last.status)
    })
  }
  show(null, last.status)
  return {
    update(sample: LiveSample) {
      show(sample.status === 'comm_lost' ? null : sample.value, sample.status)
    },
    destroy() {
      destroyed = true
      shell.destroy()
    },
  }
}

export const gaugeModule: WidgetModule = {
  type: 'gauge',
  label: 'Jauge',
  designSample: { value: 62, status: 'ok' },
  defaults: () => ({ x: 0, y: 0, w: 180, h: 112, bind: { format: '0', unit: true }, style: {}, rules: [], min: 0, max: 100 }),
  editorSchema: [
    { key: 'bind.point', label: 'Point', type: 'point' },
    { key: 'min', label: 'Minimum', type: 'number', default: 0 },
    { key: 'max', label: 'Maximum', type: 'number', default: 100 },
    { key: 'bind.format', label: 'Format', type: 'text', optional: true, placeholder: '0' },
    { key: 'bind.unit', label: "Afficher l'unité", type: 'boolean', default: true },
  ],
  render,
}
