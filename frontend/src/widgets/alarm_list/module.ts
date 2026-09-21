import type { WidgetDef } from '../../synoptic/model'
import type { WidgetContext, WidgetModule } from '../base'
import type { WidgetInstance } from '../types'
import { placeholder } from '../trend/module'
import { render as renderAlarms } from './alarm_list'
import { editorSchema } from './schema'

/** Adaptateur du widget `alarm_list` au contrat des synoptiques. */
export const alarmListModule: WidgetModule = {
  type: 'alarm_list',
  label: 'Alarmes',
  defaults: () => ({ x: 0, y: 0, w: 720, h: 260, bind: {}, style: {}, rules: [], states: 'open', maxRows: 20, allowAck: true }),
  editorSchema,
  render(container: HTMLElement, widget: WidgetDef, ctx: WidgetContext): WidgetInstance {
    if (ctx.design) return placeholder(container, widget, 'Liste des alarmes')
    try {
      const instance = renderAlarms(
        container,
        { ...widget, allowAck: widget.allowAck !== false && ctx.canWrite, theme: widget.theme ?? ctx.theme },
        { api: ctx.api, live: ctx.live },
      )
      return { update: () => undefined, destroy: () => instance.destroy() }
    } catch (error) {
      return placeholder(container, widget, error instanceof Error ? error.message : 'Configuration invalide')
    }
  },
}
