import type { LiveSample } from '../../api/types'
import type { WidgetDef } from '../../synoptic/model'
import {
  boundPoint,
  element,
  formatNumber,
  injectWidgetStyles,
  Shell,
  stringProp,
  type WidgetContext,
  type WidgetModule,
} from '../base'
import type { WidgetInstance } from '../types'

/** Valeur formatée avec son unité (`bind.format`, `bind.unit`). */
function render(container: HTMLElement, widget: WidgetDef, ctx: WidgetContext): WidgetInstance {
  injectWidgetStyles()
  const shell = new Shell(container, widget, 'w-value')
  const caption = stringProp(widget, 'label')
  if (caption) shell.root.append(element('div', 'caption', caption))
  const line = element('div', 'number')
  const number = element('span', '', '—')
  const unit = element('span', 'unit')
  line.append(number, unit)
  shell.root.append(line)

  const point = boundPoint(widget)
  const showUnit = widget.bind?.unit !== false
  let destroyed = false
  if (point && showUnit && ctx.pointInfo) {
    void ctx.pointInfo(point).then((info) => {
      if (!destroyed && info?.unit) unit.textContent = info.unit
    })
  }
  const show = (value: number | null, status: string) => {
    number.textContent = formatNumber(value, widget.bind?.format)
    shell.root.dataset.status = status
    shell.refresh(value, status)
  }
  show(null, point ? 'unknown' : 'unbound')

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

export const valueModule: WidgetModule = {
  type: 'value',
  label: 'Valeur',
  designSample: { value: 21.46, status: 'ok' },
  defaults: () => ({
    x: 0,
    y: 0,
    w: 160,
    h: 56,
    bind: { format: '0.0', unit: true },
    style: { fontSize: 28 },
    rules: [],
  }),
  editorSchema: [
    { key: 'bind.point', label: 'Point', type: 'point' },
    { key: 'label', label: 'Libellé', type: 'text', optional: true },
    { key: 'bind.format', label: 'Format', type: 'text', optional: true, placeholder: '0.0' },
    { key: 'bind.unit', label: "Afficher l'unité", type: 'boolean', default: true },
  ],
  render,
}
