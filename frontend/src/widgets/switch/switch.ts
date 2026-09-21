import type { LiveSample } from '../../api/types'
import type { WidgetDef } from '../../synoptic/model'
import {
  boundPoint,
  element,
  injectWidgetStyles,
  numberProp,
  Shell,
  stringProp,
  type WidgetContext,
  type WidgetModule,
} from '../base'
import type { WidgetInstance } from '../types'

const ERROR_MS = 5000

/** Commande binaire : le clic demande confirmation puis écrit 1 ou 0 avec la priorité configurée. */
function render(container: HTMLElement, widget: WidgetDef, ctx: WidgetContext): WidgetInstance {
  injectWidgetStyles()
  const shell = new Shell(container, widget, 'w-switch')
  const point = boundPoint(widget)
  const priority = numberProp(widget, 'priority', 8)
  const caption = stringProp(widget, 'label')
  const needConfirm = widget.confirm !== false

  const button = element('button')
  button.type = 'button'
  button.setAttribute('role', 'switch')
  const track = element('span', 'track')
  track.append(element('span', 'knob'))
  const text = element('span', 'text')
  button.append(track, text)
  const error = element('span', 'error')
  error.setAttribute('role', 'alert')
  shell.root.append(button, error)

  let value: number | null = null
  let pending = false
  let destroyed = false
  let errorTimer: ReturnType<typeof setTimeout> | undefined

  const allowed = Boolean(point && ctx.write && ctx.canWrite && !ctx.design)
  const paint = () => {
    const on = value !== null && value >= 0.5
    shell.root.dataset.state = value === null ? 'unknown' : on ? 'on' : 'off'
    button.setAttribute('aria-checked', String(on))
    const state = value === null ? 'inconnu' : on ? 'Marche' : 'Arrêt'
    text.textContent = caption ? `${caption} : ${state}` : state
    button.disabled = !allowed || pending
    button.title = !ctx.canWrite ? 'Droits insuffisants pour commander' : !point ? 'Aucun point lié' : ''
  }
  const fail = (message: string) => {
    error.textContent = message
    clearTimeout(errorTimer)
    errorTimer = setTimeout(() => {
      error.textContent = ''
    }, ERROR_MS)
  }

  button.addEventListener('click', async () => {
    if (!allowed || pending || !point || !ctx.write) return
    const next = value !== null && value >= 0.5 ? 0 : 1
    const question = `${caption || 'Commande'} : passer à « ${next ? 'Marche' : 'Arrêt'} » ?`
    if (needConfirm && !(await ctx.confirm(question))) return
    pending = true
    error.textContent = ''
    paint()
    try {
      await ctx.write(point, next, priority)
    } catch (exception) {
      if (!destroyed) fail(`Échec : ${exception instanceof Error ? exception.message : String(exception)}`)
    } finally {
      pending = false
      if (!destroyed) paint()
    }
  })

  paint()
  shell.refresh(null, point ? 'unknown' : 'unbound')
  return {
    update(sample: LiveSample) {
      value = sample.status === 'comm_lost' ? null : sample.value
      shell.root.dataset.status = sample.status
      paint()
      shell.refresh(value, sample.status)
    },
    destroy() {
      destroyed = true
      clearTimeout(errorTimer)
      shell.destroy()
    },
  }
}

export const switchModule: WidgetModule = {
  type: 'switch',
  label: 'Commande',
  designSample: { value: 1, status: 'ok' },
  defaults: () => ({ x: 0, y: 0, w: 200, h: 36, bind: {}, style: {}, rules: [], label: 'Ventilateur', priority: 8, confirm: true }),
  editorSchema: [
    { key: 'bind.point', label: 'Point commandé', type: 'point' },
    { key: 'label', label: 'Libellé', type: 'text', optional: true },
    { key: 'priority', label: 'Priorité BACnet (1 à 16)', type: 'number', min: 1, max: 16, step: 1, default: 8 },
    { key: 'confirm', label: 'Demander confirmation', type: 'boolean', default: true },
  ],
  render,
}
