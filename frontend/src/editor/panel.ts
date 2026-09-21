/** Panneau de propriétés : généré depuis les `editorSchema` des widgets. */

import type { PointInfo, PointsApi, SynopticSummary } from '../api/types'
import { isValidExpression } from '../synoptic/expressions'
import { boundPoints, MAX_IMAGE_BYTES, type Rule, type WidgetDef } from '../synoptic/model'
import { element } from '../widgets/base'
import { MODULES } from '../widgets/registry'
import type { EditorField } from '../widgets/types'
import type { AlignMode } from './geometry'
import { openPointPicker } from './picker'
import type { EditorState } from './state'

export interface PanelDeps {
  state: EditorState
  api: PointsApi & { synoptics?: () => Promise<SynopticSummary[]> }
  currentSlug: () => string | null
}

const ALIGNMENTS: [AlignMode, string][] = [
  ['left', 'Aligner à gauche'],
  ['center', 'Centrer horizontalement'],
  ['right', 'Aligner à droite'],
  ['top', 'Aligner en haut'],
  ['middle', 'Centrer verticalement'],
  ['bottom', 'Aligner en bas'],
]
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml']

function getPath(widget: WidgetDef, path: string): unknown {
  return path.split('.').reduce<unknown>((holder, key) => (holder as Record<string, unknown> | undefined)?.[key], widget)
}

/** Lit un fichier image en data URL ; rejette avec un message clair (type ou taille refusés). */
export function readImageFile(file: File): Promise<string> {
  if (!IMAGE_TYPES.includes(file.type)) return Promise.reject(new Error('Format refusé : PNG, JPEG, WebP ou SVG.'))
  if (file.size > MAX_IMAGE_BYTES) return Promise.reject(new Error('Image trop lourde : 5 Mo au maximum.'))
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('Lecture du fichier impossible.'))
    reader.readAsDataURL(file)
  })
}

export class Panel {
  private signature = ''
  private readonly pointNames = new Map<string, Promise<PointInfo | null>>()
  private synoptics: Promise<SynopticSummary[]> | null = null

  constructor(private readonly host: HTMLElement, private readonly deps: PanelDeps) {
    host.classList.add('ed-panel')
    host.setAttribute('aria-label', 'Propriétés')
    deps.state.onChange(() => this.sync())
    this.render()
  }

  /** Reconstruit le panneau si la structure change ; sinon actualise seulement les champs de géométrie. */
  private sync(): void {
    const state = this.deps.state
    const signature = [...state.selection].join(',') + '|' + state.doc.widgets.map((w) => `${w.id}:${w.rules.length}`).join(',')
    if (signature !== this.signature) return this.render()
    const single = state.selected.length === 1 ? state.selected[0] : undefined
    if (!single) return
    for (const key of ['x', 'y', 'w', 'h'] as const) {
      const input = this.host.querySelector<HTMLInputElement>(`input[data-geo="${key}"]`)
      if (input && document.activeElement !== input) input.value = String(Math.round(single[key] * 100) / 100)
    }
  }

  private pointInfo(id: string): Promise<PointInfo | null> {
    let info = this.pointNames.get(id)
    if (!info) this.pointNames.set(id, (info = this.deps.api.point(id).catch(() => null)))
    return info
  }

  render(): void {
    const { state } = this.deps
    this.signature = [...state.selection].join(',') + '|' + state.doc.widgets.map((w) => `${w.id}:${w.rules.length}`).join(',')
    const selected = state.selected
    if (selected.length === 0) this.host.replaceChildren(this.canvasSection())
    else if (selected.length === 1) this.host.replaceChildren(...this.widgetSections(selected[0] as WidgetDef))
    else this.host.replaceChildren(this.multiSection(selected))
  }

  // -- sections ----------------------------------------------------------------------------
  private section(title: string, ...children: HTMLElement[]): HTMLElement {
    const section = element('section', 'ed-section')
    section.append(element('h3', '', title), ...children)
    return section
  }

  private row(label: string, control: HTMLElement, hint?: string): HTMLElement {
    const row = element('label', 'ed-row')
    row.append(element('span', 'ed-row__label', label), control)
    if (hint) row.append(element('span', 'ed-row__hint', hint))
    return row
  }

  private numberInput(value: number, onInput: (value: number) => void, attributes: Partial<HTMLInputElement> = {}): HTMLInputElement {
    const input = element('input')
    input.type = 'number'
    Object.assign(input, attributes)
    input.value = String(Math.round(value * 100) / 100)
    input.addEventListener('input', () => {
      if (!Number.isNaN(input.valueAsNumber)) onInput(input.valueAsNumber)
    })
    return input
  }

  private canvasSection(): HTMLElement {
    const { state } = this.deps
    const doc = state.doc
    const name = element('input')
    name.type = 'text'
    name.value = doc.name
    name.maxLength = 255
    name.addEventListener('input', () => state.setName(name.value))
    const background = element('input')
    background.type = 'color'
    background.value = /^#[0-9a-f]{6}$/i.test(doc.canvas.background) ? doc.canvas.background : '#0f172a'
    background.addEventListener('input', () => state.setCanvas({ background: background.value }))

    const imageStatus = element('span', 'ed-row__hint', doc.canvas.bg_image ? 'Image de fond définie' : '')
    imageStatus.setAttribute('role', 'status')
    const file = element('input')
    file.type = 'file'
    file.accept = IMAGE_TYPES.join(',')
    file.setAttribute('aria-label', 'Importer une image de fond')
    file.addEventListener('change', async () => {
      const chosen = file.files?.[0]
      if (!chosen) return
      try {
        state.setCanvas({ bg_image: await readImageFile(chosen) })
        imageStatus.textContent = 'Image de fond définie'
      } catch (error) {
        imageStatus.textContent = error instanceof Error ? error.message : String(error)
        file.value = ''
      }
    })
    const remove = element('button', '', "Retirer l'image")
    remove.type = 'button'
    remove.addEventListener('click', () => {
      state.setCanvas({ bg_image: null })
      imageStatus.textContent = ''
    })

    const grid = this.numberInput(state.grid, (v) => state.setGrid(v, state.snapping), { min: '1', max: '200' } as Partial<HTMLInputElement>)
    return this.section(
      'Synoptique',
      this.row('Nom', name),
      this.row('Largeur', this.numberInput(doc.canvas.width, (v) => state.setCanvas({ width: Math.round(v) }), { min: '320', max: '8000' } as Partial<HTMLInputElement>)),
      this.row('Hauteur', this.numberInput(doc.canvas.height, (v) => state.setCanvas({ height: Math.round(v) }), { min: '240', max: '8000' } as Partial<HTMLInputElement>)),
      this.row('Couleur de fond', background),
      this.row('Image de fond', file, 'PNG, JPEG, WebP ou SVG — 5 Mo au plus'),
      imageStatus,
      remove,
      this.row('Pas de la grille (px)', grid),
    )
  }

  private multiSection(selected: WidgetDef[]): HTMLElement {
    const { state } = this.deps
    const align = element('div', 'ed-buttons')
    for (const [mode, label] of ALIGNMENTS) {
      const button = element('button', '', label)
      button.type = 'button'
      button.dataset.align = mode
      button.addEventListener('click', () => state.alignSelected(mode))
      align.append(button)
    }
    const distribute = element('div', 'ed-buttons')
    for (const [axis, label] of [['horizontal', 'Répartir horizontalement'], ['vertical', 'Répartir verticalement']] as const) {
      const button = element('button', '', label)
      button.type = 'button'
      button.disabled = selected.length < 3
      button.addEventListener('click', () => state.distributeSelected(axis))
      distribute.append(button)
    }
    const remove = element('button', 'danger', `Supprimer les ${selected.length} widgets`)
    remove.type = 'button'
    remove.addEventListener('click', () => state.deleteSelected())
    return this.section(`${selected.length} widgets sélectionnés`, align, distribute, remove)
  }

  private widgetSections(widget: WidgetDef): HTMLElement[] {
    const { state } = this.deps
    const module = MODULES[widget.type]
    const geometry = element('div', 'ed-grid')
    for (const [key, label] of [['x', 'X'], ['y', 'Y'], ['w', 'Largeur'], ['h', 'Hauteur']] as const) {
      const input = this.numberInput(widget[key], (v) => state.setGeometry(widget.id, { [key]: key === 'w' || key === 'h' ? Math.max(8, v) : v }), {
        step: '1',
      } as Partial<HTMLInputElement>)
      input.dataset.geo = key
      input.setAttribute('aria-label', label)
      const cell = element('label', 'ed-cell')
      cell.append(element('span', '', label), input)
      geometry.append(cell)
    }
    const properties = module.editorSchema.map((field) => this.fieldRow(widget, field))
    const actions = element('div', 'ed-buttons')
    for (const [label, action, danger] of [
      ['Dupliquer', () => state.duplicate(), false],
      ['Premier plan', () => state.bringToFront(), false],
      ['Arrière-plan', () => state.sendToBack(), false],
      ['Supprimer', () => state.deleteSelected(), true],
    ] as [string, () => void, boolean][]) {
      const button = element('button', danger ? 'danger' : '', label)
      button.type = 'button'
      button.addEventListener('click', action)
      actions.append(button)
    }
    const sections = [
      this.section(`${module.label} (${widget.id})`, ...properties),
      this.section('Position et taille', geometry),
      this.styleSection(widget),
      this.rulesSection(widget),
    ]
    if (widget.type !== 'link') sections.push(this.linkSection(widget))
    sections.push(this.section('Actions', actions))
    return sections
  }

  private styleSection(widget: WidgetDef): HTMLElement {
    const { state } = this.deps
    const rows: HTMLElement[] = []
    if (['value', 'label', 'gauge', 'indicator', 'switch', 'setpoint', 'link'].includes(widget.type)) {
      rows.push(this.row('Couleur du texte', this.colorControl(String(widget.style.color ?? ''), (v) => state.setProperty(widget.id, 'style.color', v))))
    }
    if (['value', 'label', 'link'].includes(widget.type)) {
      const size = this.numberInput(typeof widget.style.fontSize === 'number' ? widget.style.fontSize : 0, (v) => state.setProperty(widget.id, 'style.fontSize', v > 0 ? v : undefined), { min: '8', max: '200' } as Partial<HTMLInputElement>)
      if (typeof widget.style.fontSize !== 'number') size.value = ''
      rows.push(this.row('Taille du texte (px)', size))
    }
    if (widget.type === 'label') {
      const align = element('select')
      for (const [value, label] of [['left', 'Gauche'], ['center', 'Centre'], ['right', 'Droite']]) align.add(new Option(label, value, false, widget.style.textAlign === value))
      align.addEventListener('change', () => state.setProperty(widget.id, 'style.textAlign', align.value))
      rows.push(this.row('Alignement', align))
    }
    return this.section('Style', ...rows)
  }

  private rulesSection(widget: WidgetDef): HTMLElement {
    const { state } = this.deps
    const rules: Rule[] = widget.rules
    const commit = (next: Rule[]) => state.setRules(widget.id, next)
    const list = element('div', 'ed-rules')
    rules.forEach((rule, index) => {
      const when = element('input')
      when.type = 'text'
      when.value = rule.when
      when.setAttribute('aria-label', `Condition de la règle ${index + 1}`)
      when.placeholder = "value > 28 && status == 'ok'"
      const message = element('span', 'ed-row__hint')
      message.setAttribute('role', 'alert')
      const validate = () => {
        const valid = isValidExpression(when.value)
        when.setAttribute('aria-invalid', String(!valid))
        message.textContent = valid ? '' : 'Condition invalide (exemple : value > 28)'
      }
      validate()
      when.addEventListener('input', () => {
        validate()
        commit(rules.map((r, i) => (i === index ? { ...r, when: when.value } : r)))
      })
      const style = (key: string, control: HTMLElement, label: string) => this.row(label, control)
      const color = this.colorControl(String(rule.style.color ?? ''), (v) =>
        commit(rules.map((r, i) => (i === index ? { ...r, style: withKey(r.style, 'color', v) } : r))),
      )
      const opacity = this.numberInput(typeof rule.style.opacity === 'number' ? rule.style.opacity : 1, (v) =>
        commit(rules.map((r, i) => (i === index ? { ...r, style: withKey(r.style, 'opacity', Math.min(1, Math.max(0, v))) } : r))),
        { min: '0', max: '1', step: '0.1' } as Partial<HTMLInputElement>,
      )
      const remove = element('button', 'danger', 'Supprimer la règle')
      remove.type = 'button'
      remove.addEventListener('click', () => commit(rules.filter((_, i) => i !== index)))
      const card = element('div', 'ed-rule')
      card.append(this.row(`Règle ${index + 1} : quand`, when), message, style('color', color, 'couleur'), style('opacity', opacity, 'opacité'), remove)
      list.append(card)
    })
    const add = element('button', '', 'Ajouter une règle')
    add.type = 'button'
    add.addEventListener('click', () => commit([...rules, { when: 'value > 0', style: { color: '#ef4444' } }]))
    return this.section('Règles d’affichage', list, add)
  }

  private linkSection(widget: WidgetDef): HTMLElement {
    const { state } = this.deps
    return this.section('Navigation', this.row('Ouvre le synoptique', this.targetControl(state.linkOf(widget.id) ?? '', (v) => state.setLink(widget.id, v || null))))
  }

  // -- champs ------------------------------------------------------------------------------
  private fieldRow(widget: WidgetDef, field: EditorField): HTMLElement {
    const { state } = this.deps
    const set = (value: unknown) => state.setProperty(widget.id, field.key, value)
    const current = getPath(widget, field.key)
    switch (field.type) {
      case 'text': {
        const input = element('input')
        input.type = 'text'
        input.value = typeof current === 'string' ? current : ''
        input.placeholder = field.placeholder ?? ''
        input.addEventListener('input', () => set(input.value))
        return this.row(field.label, input)
      }
      case 'number': {
        const input = this.numberInput(typeof current === 'number' ? current : (field.default ?? 0), set, { min: field.min ?? '', max: field.max ?? '', step: field.step ?? 'any' } as unknown as Partial<HTMLInputElement>)
        if (typeof current !== 'number') input.value = field.default !== undefined ? String(field.default) : ''
        if (field.optional) input.addEventListener('input', () => Number.isNaN(input.valueAsNumber) && set(undefined))
        return this.row(field.label, input)
      }
      case 'boolean': {
        const input = element('input')
        input.type = 'checkbox'
        input.checked = typeof current === 'boolean' ? current : field.default
        input.addEventListener('change', () => set(input.checked))
        return this.row(field.label, input)
      }
      case 'select': {
        const select = element('select')
        for (const option of field.options) select.add(new Option(option.label, option.value, false, (current ?? field.default) === option.value))
        select.addEventListener('change', () => set(select.value))
        return this.row(field.label, select)
      }
      case 'color':
        return this.row(field.label, this.colorControl(typeof current === 'string' ? current : '', set))
      case 'point':
        return this.row(field.label, this.pointControl(typeof current === 'string' ? current : '', set))
      case 'points':
        return this.pointsControl(widget, field)
      case 'image':
        return this.row(field.label, this.imageControl(typeof current === 'string' ? current : '', set))
      case 'target':
        return this.row(field.label, this.targetControl(typeof current === 'string' ? current : '', set))
    }
  }

  private colorControl(value: string, onChange: (value: string | undefined) => void): HTMLElement {
    const wrap = element('span', 'ed-color')
    const input = element('input')
    input.type = 'color'
    input.value = /^#[0-9a-f]{6}$/i.test(value) ? value : '#ffffff'
    const clear = element('button', '', 'Aucune')
    clear.type = 'button'
    clear.hidden = value === ''
    input.addEventListener('input', () => {
      clear.hidden = false
      onChange(input.value)
    })
    clear.addEventListener('click', () => {
      clear.hidden = true
      onChange(undefined)
    })
    wrap.append(input, clear)
    return wrap
  }

  private pointControl(value: string, onChange: (value: string | undefined) => void): HTMLElement {
    const wrap = element('span', 'ed-point')
    const button = element('button', '', value ? '…' : 'Choisir un point…')
    button.type = 'button'
    button.dataset.role = 'point-picker'
    if (value) {
      void this.pointInfo(value).then((info) => {
        button.textContent = info ? `${info.path ?? info.name}` : `Point introuvable (${value.slice(0, 8)}…)`
        button.title = info ? `${info.name}${info.unit ? ` (${info.unit})` : ''}` : ''
      })
    }
    button.addEventListener('click', async () => {
      const chosen = await openPointPicker(this.deps.api, { title: 'Choisir un point' })
      if (chosen) {
        this.pointNames.set(chosen.id, Promise.resolve(chosen))
        onChange(chosen.id)
      }
    })
    wrap.append(button)
    if (value) {
      const clear = element('button', '', 'Délier')
      clear.type = 'button'
      clear.addEventListener('click', () => onChange(undefined))
      wrap.append(clear)
    }
    return wrap
  }

  private pointsControl(widget: WidgetDef, field: Extract<EditorField, { type: 'points' }>): HTMLElement {
    const { state } = this.deps
    const entries = (Array.isArray(widget.bind.points) ? widget.bind.points : []) as { point: string; label?: string }[]
    const wrap = element('div', 'ed-points')
    wrap.append(element('span', 'ed-row__label', `${field.label} (${entries.length}/${field.max})`))
    const commit = (next: typeof entries) => state.setProperty(widget.id, 'bind.points', next)
    entries.forEach((entry, index) => {
      const row = element('div', 'ed-point-row')
      const name = element('span', 'name', entry.point.slice(0, 8))
      void this.pointInfo(entry.point).then((info) => {
        name.textContent = info?.path ?? info?.name ?? name.textContent
      })
      const label = element('input')
      label.type = 'text'
      label.placeholder = 'Libellé'
      label.value = entry.label ?? ''
      label.setAttribute('aria-label', `Libellé du point ${index + 1}`)
      label.addEventListener('input', () => commit(entries.map((e, i) => (i === index ? { ...e, label: label.value || undefined } : e))))
      const remove = element('button', '', 'Retirer')
      remove.type = 'button'
      remove.addEventListener('click', () => commit(entries.filter((_, i) => i !== index)))
      row.append(name, label, remove)
      wrap.append(row)
    })
    const add = element('button', '', 'Ajouter un point…')
    add.type = 'button'
    add.dataset.role = 'point-picker'
    add.disabled = entries.length >= field.max
    add.addEventListener('click', async () => {
      const chosen = await openPointPicker(this.deps.api, { title: 'Ajouter un point à la courbe' })
      if (!chosen || entries.some((e) => e.point === chosen.id)) return
      this.pointNames.set(chosen.id, Promise.resolve(chosen))
      commit([...entries, { point: chosen.id }])
    })
    wrap.append(add)
    return wrap
  }

  private imageControl(value: string, onChange: (value: string | undefined) => void): HTMLElement {
    const wrap = element('span', 'ed-image')
    const status = element('span', 'ed-row__hint', value ? 'Image définie' : '')
    status.setAttribute('role', 'status')
    const file = element('input')
    file.type = 'file'
    file.accept = IMAGE_TYPES.join(',')
    file.addEventListener('change', async () => {
      const chosen = file.files?.[0]
      if (!chosen) return
      try {
        onChange(await readImageFile(chosen))
      } catch (error) {
        status.textContent = error instanceof Error ? error.message : String(error)
        file.value = ''
      }
    })
    wrap.append(file, status)
    return wrap
  }

  private targetControl(value: string, onChange: (value: string) => void): HTMLElement {
    const select = element('select')
    select.add(new Option('Aucun', ''))
    const fill = async () => {
      this.synoptics ??= this.deps.api.synoptics ? this.deps.api.synoptics() : Promise.resolve([])
      try {
        for (const s of await this.synoptics) {
          if (s.slug === this.deps.currentSlug()) continue
          select.add(new Option(s.name, `synoptic:${s.slug}`, false, value === `synoptic:${s.slug}`))
        }
        if (value && ![...select.options].some((o) => o.value === value)) select.add(new Option(`${value} (introuvable)`, value, true, true))
      } catch {
        // liste indisponible : la valeur courante reste modifiable à la main plus tard
      }
    }
    void fill()
    select.addEventListener('change', () => onChange(select.value))
    return select
  }
}

function withKey(style: Rule['style'], key: string, value: string | number | undefined): Rule['style'] {
  const next = { ...style }
  if (value === undefined) delete next[key]
  else next[key] = value
  return next
}

export { boundPoints }
