import type { WidgetDef } from '../../synoptic/model'
import { injectWidgetStyles, Shell, staticInstance, stringProp, type WidgetContext, type WidgetModule } from '../base'
import type { WidgetInstance } from '../types'

const ALIGN: Record<string, string> = { left: 'flex-start', center: 'center', right: 'flex-end' }

/** Texte statique. */
function render(container: HTMLElement, widget: WidgetDef, _ctx: WidgetContext): WidgetInstance {
  injectWidgetStyles()
  const shell = new Shell(container, widget, 'w-label')
  shell.root.textContent = stringProp(widget, 'text', 'Texte')
  shell.root.style.setProperty('--w-text-align-flex', ALIGN[String(widget.style?.textAlign)] ?? 'flex-start')
  shell.refresh(null, 'static')
  return staticInstance(shell)
}

export const labelModule: WidgetModule = {
  type: 'label',
  label: 'Texte',
  defaults: () => ({ x: 0, y: 0, w: 200, h: 32, bind: {}, style: { fontSize: 18 }, rules: [], text: 'Texte' }),
  editorSchema: [{ key: 'text', label: 'Texte', type: 'text' }],
  render,
}
