import type { WidgetDef } from '../../synoptic/model'
import { element, injectWidgetStyles, Shell, staticInstance, stringProp, type WidgetContext, type WidgetModule } from '../base'
import type { WidgetInstance } from '../types'

const DATA_IMAGE = /^data:image\/(png|jpeg|svg\+xml|webp);base64,[A-Za-z0-9+/=]+$/

/** Image ou pictogramme (PNG, JPEG, WebP, SVG en data URL) : jamais interprété comme du HTML. */
function render(container: HTMLElement, widget: WidgetDef, _ctx: WidgetContext): WidgetInstance {
  injectWidgetStyles()
  const shell = new Shell(container, widget, 'w-image')
  const src = stringProp(widget, 'src')
  if (DATA_IMAGE.test(src)) {
    const image = element('img')
    image.src = src
    image.alt = stringProp(widget, 'alt')
    image.draggable = false
    shell.root.style.setProperty('--fit', widget.fit === 'cover' ? 'cover' : 'contain')
    shell.root.append(image)
  } else {
    shell.root.classList.add('w-placeholder')
    shell.root.textContent = 'Aucune image'
  }
  shell.refresh(null, 'static')
  return staticInstance(shell)
}

export const imageModule: WidgetModule = {
  type: 'image',
  label: 'Image',
  defaults: () => ({ x: 0, y: 0, w: 160, h: 120, bind: {}, style: {}, rules: [], fit: 'contain', alt: '' }),
  editorSchema: [
    { key: 'src', label: 'Image (PNG, JPEG, SVG — 5 Mo max)', type: 'image' },
    { key: 'alt', label: 'Description', type: 'text', optional: true },
    {
      key: 'fit',
      label: 'Ajustement',
      type: 'select',
      default: 'contain',
      options: [
        { value: 'contain', label: 'Entière' },
        { value: 'cover', label: 'Remplir' },
      ],
    },
  ],
  render,
}
