import type { LiveSample } from '../../api/types'
import type { WidgetDef } from '../../synoptic/model'
import {
  boundPoint,
  element,
  formatNumber,
  injectWidgetStyles,
  numberProp,
  Shell,
  stringProp,
  type WidgetContext,
  type WidgetModule,
} from '../base'
import type { WidgetInstance } from '../types'

const MESSAGE_MS = 4000

function optionalNumber(widget: WidgetDef, key: string): number | null {
  const value = widget[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** Consigne numérique bornée ; les bornes viennent du widget, à défaut de celles du point. */
function render(container: HTMLElement, widget: WidgetDef, ctx: WidgetContext): WidgetInstance {
  injectWidgetStyles()
  const shell = new Shell(container, widget, 'w-setpoint')
  const point = boundPoint(widget)
  const priority = numberProp(widget, 'priority', 8)
  const caption = stringProp(widget, 'label')
  let min = optionalNumber(widget, 'min')
  let max = optionalNumber(widget, 'max')
  const step = numberProp(widget, 'step', 0.5)
  let unit = ''

  const title = element('div', 'caption', caption)
  title.hidden = !caption
  const input = element('input')
  input.type = 'number'
  input.step = String(step)
  input.setAttribute('aria-label', caption || 'Consigne')
  const apply = element('button', '', 'Appliquer')
  apply.type = 'button'
  const release = element('button', '', 'Relâcher')
  release.type = 'button'
  release.hidden = widget.allowRelease !== true
  const row = element('div', 'row')
  row.append(input, apply, release)
  const message = element('div', 'msg')
  message.setAttribute('role', 'status')
  shell.root.append(title, row, message)

  const allowed = Boolean(point && ctx.write && ctx.canWrite && !ctx.design)
  let pending = false
  let destroyed = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const paint = () => {
    const disabled = !allowed || pending
    input.disabled = disabled
    apply.disabled = disabled
    release.disabled = disabled
    input.title = !ctx.canWrite ? 'Droits insuffisants pour commander' : ''
    if (min !== null) input.min = String(min)
    if (max !== null) input.max = String(max)
  }
  const say = (text: string, isError = false) => {
    message.textContent = text
    message.classList.toggle('error', isError)
    clearTimeout(timer)
    timer = setTimeout(() => {
      message.textContent = ''
    }, MESSAGE_MS)
  }
  const send = async (value: number | null) => {
    if (!allowed || pending || !point || !ctx.write) return
    if (value !== null) {
      if (min !== null && value < min) return say(`Minimum : ${formatNumber(min, '0.##')} ${unit}`.trim(), true)
      if (max !== null && value > max) return say(`Maximum : ${formatNumber(max, '0.##')} ${unit}`.trim(), true)
    }
    if (widget.confirm === true) {
      const what = value === null ? 'relâcher la consigne' : `envoyer ${formatNumber(value, '0.##')} ${unit}`.trim()
      if (!(await ctx.confirm(`${caption || 'Consigne'} : ${what} ?`))) return
    }
    pending = true
    paint()
    try {
      await ctx.write(point, value, priority)
      if (!destroyed) say(value === null ? 'Consigne relâchée' : 'Consigne envoyée')
    } catch (exception) {
      if (!destroyed) say(`Échec : ${exception instanceof Error ? exception.message : String(exception)}`, true)
    } finally {
      pending = false
      if (!destroyed) paint()
    }
  }
  apply.addEventListener('click', () => {
    const value = input.valueAsNumber
    if (Number.isNaN(value)) return say('Saisissez un nombre', true)
    void send(value)
  })
  release.addEventListener('click', () => void send(null))
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') apply.click()
  })

  if (point && ctx.pointInfo) {
    void ctx.pointInfo(point).then((info) => {
      if (destroyed || !info) return
      unit = info.unit ?? ''
      if (min === null && typeof info.write_min === 'number') min = info.write_min
      if (max === null && typeof info.write_max === 'number') max = info.write_max
      paint()
    })
  }
  paint()
  shell.refresh(null, point ? 'unknown' : 'unbound')
  return {
    update(sample: LiveSample) {
      shell.root.dataset.status = sample.status
      // La valeur courante est la consigne en vigueur ; on ne l'écrase pas pendant une saisie.
      if (document.activeElement !== input && sample.value !== null && sample.status !== 'comm_lost') {
        input.value = String(Math.round(sample.value * 1000) / 1000)
      }
      shell.refresh(sample.value, sample.status)
    },
    destroy() {
      destroyed = true
      clearTimeout(timer)
      shell.destroy()
    },
  }
}

export const setpointModule: WidgetModule = {
  type: 'setpoint',
  label: 'Consigne',
  designSample: { value: 21, status: 'ok' },
  defaults: () => ({ x: 0, y: 0, w: 220, h: 64, bind: {}, style: {}, rules: [], label: 'Consigne', priority: 8, step: 0.5, allowRelease: false }),
  editorSchema: [
    { key: 'bind.point', label: 'Point commandé', type: 'point' },
    { key: 'label', label: 'Libellé', type: 'text', optional: true },
    { key: 'min', label: 'Minimum', type: 'number', optional: true },
    { key: 'max', label: 'Maximum', type: 'number', optional: true },
    { key: 'step', label: 'Pas', type: 'number', min: 0.001, default: 0.5 },
    { key: 'priority', label: 'Priorité BACnet (1 à 16)', type: 'number', min: 1, max: 16, step: 1, default: 8 },
    { key: 'confirm', label: 'Demander confirmation', type: 'boolean', default: false },
    { key: 'allowRelease', label: 'Bouton « Relâcher »', type: 'boolean', default: false },
  ],
  render,
}
