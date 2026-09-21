import type { WidgetDef } from '../../synoptic/model'
import { element, injectWidgetStyles, Shell, staticInstance, stringProp, type WidgetContext, type WidgetModule } from '../base'
import type { WidgetInstance } from '../types'

/** Navigation vers un autre synoptique (`target: "synoptic:<slug>"`). */
function render(container: HTMLElement, widget: WidgetDef, ctx: WidgetContext): WidgetInstance {
  injectWidgetStyles()
  const shell = new Shell(container, widget, 'w-link')
  const target = stringProp(widget, 'target')
  const button = element('button', '', stringProp(widget, 'text', 'Ouvrir'))
  button.type = 'button'
  button.disabled = !target
  button.title = target ? `Ouvrir ${target.replace('synoptic:', '')}` : 'Aucune cible'
  button.addEventListener('click', () => {
    if (target && !ctx.design) ctx.navigate(target)
  })
  shell.root.append(button)
  shell.refresh(null, 'static')
  return staticInstance(shell)
}

export const linkModule: WidgetModule = {
  type: 'link',
  label: 'Lien',
  defaults: () => ({ x: 0, y: 0, w: 160, h: 40, bind: {}, style: {}, rules: [], text: 'Ouvrir', target: '' }),
  editorSchema: [
    { key: 'text', label: 'Texte', type: 'text' },
    { key: 'target', label: 'Synoptique cible', type: 'target' },
  ],
  render,
}
