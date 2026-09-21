import type { LiveSample } from '../../api/types'
import type { WidgetDef } from '../../synoptic/model'
import { boundPoint, injectWidgetStyles, Shell, staticInstance, svgElement, type WidgetContext, type WidgetModule } from '../base'
import type { WidgetInstance } from '../types'

/**
 * Rectangle, ligne ou tuyau. La couleur suit les règles (`fill`, `stroke`) ; un tuyau peut animer
 * l'écoulement (`flow`) tant que la valeur du point lié est positive.
 */
function render(container: HTMLElement, widget: WidgetDef, _ctx: WidgetContext): WidgetInstance {
  injectWidgetStyles()
  const shell = new Shell(container, widget, 'w-shape')
  const kind = widget.shape === 'line' || widget.shape === 'pipe' ? widget.shape : 'rect'
  const svg = svgElement('svg', { viewBox: '0 0 100 100', preserveAspectRatio: 'none', 'aria-hidden': 'true' })
  const horizontal = widget.w >= widget.h
  const d = horizontal ? 'M2 50 L98 50' : 'M50 2 L50 98'

  if (kind === 'rect') {
    const radius = typeof widget.style?.borderRadius === 'number' ? widget.style.borderRadius : 0
    svg.append(svgElement('rect', { class: 'shape', x: 1, y: 1, width: 98, height: 98, rx: radius, 'vector-effect': 'non-scaling-stroke' }))
  } else if (kind === 'line') {
    svg.append(svgElement('path', { class: 'shape', d, 'vector-effect': 'non-scaling-stroke' }))
  } else {
    svg.append(
      svgElement('path', { class: 'pipe', d, 'vector-effect': 'non-scaling-stroke' }),
      svgElement('path', { class: 'flow', d, 'vector-effect': 'non-scaling-stroke' }),
    )
  }
  shell.root.append(svg)
  shell.root.dataset.flow = 'off'

  const point = boundPoint(widget)
  const flows = kind === 'pipe' && widget.flow === true
  const setFlow = (value: number | null) => {
    const on = flows && (point ? value !== null && value > 0 : true)
    shell.root.dataset.flow = on ? 'on' : 'off'
  }
  setFlow(null)
  shell.refresh(null, point ? 'unknown' : 'static')
  if (!point) return staticInstance(shell)
  return {
    update(sample: LiveSample) {
      const value = sample.status === 'comm_lost' ? null : sample.value
      setFlow(value)
      shell.refresh(value, sample.status)
    },
    destroy: () => shell.destroy(),
  }
}

export const shapeModule: WidgetModule = {
  type: 'shape',
  label: 'Forme',
  designSample: { value: 1, status: 'ok' },
  defaults: () => ({ x: 0, y: 0, w: 200, h: 24, bind: {}, style: { stroke: '#38bdf8', strokeWidth: 10 }, rules: [], shape: 'pipe', flow: true }),
  editorSchema: [
    {
      key: 'shape',
      label: 'Forme',
      type: 'select',
      default: 'pipe',
      options: [
        { value: 'rect', label: 'Rectangle' },
        { value: 'line', label: 'Ligne' },
        { value: 'pipe', label: 'Tuyau' },
      ],
    },
    { key: 'style.stroke', label: 'Couleur du trait', type: 'color' },
    { key: 'style.fill', label: 'Remplissage (rectangle)', type: 'color' },
    { key: 'style.strokeWidth', label: 'Épaisseur du trait', type: 'number', min: 1, max: 40, step: 1 },
    { key: 'flow', label: "Animer l'écoulement (tuyau)", type: 'boolean', default: true },
    { key: 'bind.point', label: 'Point lié (facultatif : règles et écoulement)', type: 'point' },
  ],
  render,
}
