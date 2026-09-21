import type { WidgetDef } from '../../synoptic/model'
import { element, injectWidgetStyles, Shell, staticInstance, type WidgetContext, type WidgetModule } from '../base'
import type { WidgetInstance } from '../types'
import { editorSchema } from './schema'
import { render as renderTrend } from './trend'

/** Adaptateur du widget `trend` au contrat des synoptiques. */
export const trendModule: WidgetModule = {
  type: 'trend',
  label: 'Courbe',
  defaults: () => ({ x: 0, y: 0, w: 520, h: 280, bind: { points: [] }, style: {}, rules: [], range: '1h', live: true, rangeSelector: true }),
  editorSchema,
  render(container: HTMLElement, widget: WidgetDef, ctx: WidgetContext): WidgetInstance {
    if (ctx.design) return placeholder(container, widget, 'Courbe de tendance')
    try {
      const instance = renderTrend(
        container,
        { ...widget, theme: widget.theme ?? ctx.theme },
        { api: ctx.api, live: ctx.live },
      )
      return { update: (sample) => instance.update(sample), destroy: () => instance.destroy() }
    } catch (error) {
      return placeholder(container, widget, error instanceof Error ? error.message : 'Configuration invalide')
    }
  },
}

export function placeholder(container: HTMLElement, widget: WidgetDef, title: string): WidgetInstance {
  injectWidgetStyles()
  const shell = new Shell(container, widget, 'w-placeholder')
  shell.root.append(element('strong', '', title), element('span', '', `${widget.w} × ${widget.h}`))
  return staticInstance(shell)
}
