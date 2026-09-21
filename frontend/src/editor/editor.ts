/** Éditeur de synoptiques : palette, canevas à grille, sélection, propriétés, aperçu, versions. */

import { ApiError } from '../api/client'
import type { LiveApi, PointInfo, PointsApi, SynopticRecord, SynopticsApi, WriteApi } from '../api/types'
import { cloneDoc, documentProblems, isDarkColor, type SynopticDoc, WIDGET_TYPES, type WidgetDef } from '../synoptic/model'
import { mountViewer, type ViewerInstance } from '../viewer/viewer'
import { dialogConfirm, injectConfirmStyles } from '../viewer/confirm'
import { element, injectWidgetStyles, type WidgetApi, type WidgetContext } from '../widgets/base'
import { moduleFor, MODULES } from '../widgets/registry'
import type { WidgetInstance } from '../widgets/types'
import { HANDLES, type Handle, rectBetween, resizeRect } from './geometry'
import { Panel } from './panel'
import { EditorState } from './state'
import { injectEditorStyles } from './styles'
import { openVersions } from './versions'

export type EditorApi = WidgetApi & PointsApi & SynopticsApi & WriteApi

export interface EditorDeps {
  api: EditorApi
  live: LiveApi
  role: string
  /** Synoptique ouvert ; `null` pour en créer un. */
  record: SynopticRecord | null
  /** Retour à la liste. */
  exit: () => void
  /** Appelé après un enregistrement (le shell met à jour l'adresse de la page). */
  onSaved?: (record: SynopticRecord) => void
  confirm?: (message: string) => Promise<boolean>
}

export interface EditorInstance {
  readonly state: EditorState
  save(): Promise<void>
  destroy(): void
}

const MIN_ZOOM = 0.1
const MAX_ZOOM = 3
const DRAG_THRESHOLD_PX = 3
const PALETTE_ORDER = ['value', 'label', 'gauge', 'indicator', 'switch', 'setpoint', 'trend', 'alarm_list', 'image', 'link', 'shape'] as const

interface Item {
  el: HTMLElement
  instance: WidgetInstance
  signature: string
}

export function mountEditor(container: HTMLElement, deps: EditorDeps): EditorInstance {
  injectWidgetStyles()
  injectConfirmStyles()
  injectEditorStyles()
  const confirm = deps.confirm ?? dialogConfirm
  let record = deps.record
  const state = new EditorState(record?.doc)
  let zoom = 1
  let showGrid = true
  let previewing = false
  let previewViewer: ViewerInstance | null = null
  let destroyed = false

  // -- structure ---------------------------------------------------------------------------
  const root = element('div', 'ed')
  const toolbar = element('header', 'ed-toolbar')
  const palette = element('aside', 'ed-palette')
  palette.setAttribute('aria-label', 'Widgets')
  const viewport = element('main', 'ed-viewport')
  viewport.tabIndex = 0
  viewport.setAttribute('aria-label', 'Canevas du synoptique')
  const stage = element('div', 'ed-stage')
  const canvas = element('div', 'ed-canvas')
  const gridLayer = element('div', 'ed-grid-layer')
  const selectionLayer = element('div', 'ed-selection-layer')
  const band = element('div', 'ed-band')
  band.hidden = true
  const preview = element('div', 'ed-preview')
  preview.hidden = true
  const panelHost = element('aside')
  const statusBar = element('div', 'ed-status')
  statusBar.setAttribute('role', 'status')
  canvas.append(gridLayer, selectionLayer, band)
  stage.append(canvas)
  viewport.append(stage, preview)
  root.append(toolbar, palette, viewport, panelHost, statusBar)
  container.append(root)

  const panel = new Panel(panelHost, { state, api: deps.api, currentSlug: () => record?.slug ?? null })

  // -- barre d'outils ----------------------------------------------------------------------
  const button = (label: string, onClick: () => void, extra = ''): HTMLButtonElement => {
    const b = element('button', extra, label)
    b.type = 'button'
    b.addEventListener('click', onClick)
    return b
  }
  const back = button('← Synoptiques', () => void leave())
  const nameInput = element('input', 'ed-name')
  nameInput.type = 'text'
  nameInput.maxLength = 255
  nameInput.setAttribute('aria-label', 'Nom du synoptique')
  nameInput.addEventListener('input', () => state.setName(nameInput.value))
  const undoButton = button('Annuler', () => state.undo())
  const redoButton = button('Rétablir', () => state.redo())
  const snapBox = element('input')
  snapBox.type = 'checkbox'
  snapBox.checked = true
  snapBox.addEventListener('change', () => state.setGrid(state.grid, snapBox.checked))
  const gridBox = element('input')
  gridBox.type = 'checkbox'
  gridBox.checked = true
  gridBox.addEventListener('change', () => {
    showGrid = gridBox.checked
    renderChrome()
  })
  const check = (box: HTMLInputElement, label: string) => {
    const wrap = element('label', 'ed-check')
    wrap.append(box, element('span', '', label))
    return wrap
  }
  const zoomLabel = element('span', 'ed-zoom')
  const zoomOut = button('−', () => setZoom(zoom / 1.25))
  zoomOut.setAttribute('aria-label', 'Réduire')
  const zoomIn = button('+', () => setZoom(zoom * 1.25))
  zoomIn.setAttribute('aria-label', 'Agrandir')
  const fit = button('Ajuster', () => fitToViewport())
  const previewButton = button('Aperçu en direct', () => setPreview(!previewing))
  previewButton.setAttribute('aria-pressed', 'false')
  const versionsButton = button('Versions', () => openVersionsDialog())
  const saveButton = button('Enregistrer', () => void save(), 'primary')
  toolbar.append(back, nameInput, undoButton, redoButton, check(snapBox, 'Magnétisme'), check(gridBox, 'Grille'), zoomOut, zoomLabel, zoomIn, fit, previewButton, versionsButton, saveButton)

  // -- palette -----------------------------------------------------------------------------
  const centerOfView = () => ({
    x: (viewport.scrollLeft + viewport.clientWidth / 2 - stage.offsetLeft) / zoom || state.doc.canvas.width / 2,
    y: (viewport.scrollTop + viewport.clientHeight / 2 - stage.offsetTop) / zoom || state.doc.canvas.height / 2,
  })
  palette.append(element('h2', '', 'Widgets'))
  for (const type of PALETTE_ORDER) {
    const module = MODULES[type]
    const item = button(module.label, () => state.addWidget(type, centerOfView()), 'ed-palette__item')
    item.dataset.type = type
    item.draggable = true
    item.addEventListener('dragstart', (event) => {
      event.dataTransfer?.setData('application/x-synoptic-widget', type)
      event.dataTransfer?.setData('text/plain', type)
    })
    palette.append(item)
  }
  canvas.addEventListener('dragover', (event) => {
    if (!previewing) event.preventDefault()
  })
  canvas.addEventListener('drop', (event) => {
    const type = event.dataTransfer?.getData('application/x-synoptic-widget')
    if (previewing || !type || !(WIDGET_TYPES as readonly string[]).includes(type)) return
    event.preventDefault()
    state.addWidget(type as (typeof WIDGET_TYPES)[number], toCanvas(event))
  })

  // -- widgets en mode conception ----------------------------------------------------------
  const items = new Map<string, Item>()
  const infos = new Map<string, Promise<PointInfo | null>>()
  const designContext: WidgetContext = {
    api: deps.api,
    canWrite: false,
    navigate: () => undefined,
    theme: 'dark',
    confirm: async () => false,
    design: true,
    pointInfo: (id) => {
      let info = infos.get(id)
      if (!info) infos.set(id, (info = deps.api.point(id).catch(() => null)))
      return info
    },
  }

  const signatureOf = (widget: WidgetDef) => JSON.stringify({ ...widget, x: 0, y: 0, w: 0, h: 0 })

  function build(widget: WidgetDef): Item {
    const el = element('div', 'ed-item')
    el.dataset.widgetId = widget.id
    const module = moduleFor(widget.type) ?? MODULES.label
    const host = element('div', 'ed-item__content')
    el.append(host)
    const hit = element('div', 'ed-hit') // capte les clics : le widget lui-même n'est pas interactif ici
    el.append(hit)
    let instance: WidgetInstance
    try {
      instance = module.render(host, widget, { ...designContext, theme: isDarkColor(state.doc.canvas.background) ? 'dark' : 'light' })
      if (module.designSample) instance.update({ point: '', ts: 0, value: module.designSample.value, status: module.designSample.status })
    } catch {
      host.textContent = `Widget ${widget.id} illisible`
      instance = { update: () => undefined, destroy: () => undefined }
    }
    return { el, instance, signature: signatureOf(widget) }
  }

  function syncItems(): void {
    const doc = state.doc
    const present = new Set(doc.widgets.map((w) => w.id))
    for (const [id, item] of items) {
      if (!present.has(id)) {
        item.instance.destroy()
        item.el.remove()
        items.delete(id)
      }
    }
    doc.widgets.forEach((widget) => {
      let item = items.get(widget.id)
      if (item && item.signature !== signatureOf(widget)) {
        item.instance.destroy()
        item.el.remove()
        item = undefined
      }
      if (!item) {
        item = build(widget)
        items.set(widget.id, item)
      }
      Object.assign(item.el.style, { left: `${widget.x}px`, top: `${widget.y}px`, width: `${widget.w}px`, height: `${widget.h}px` })
      item.el.classList.toggle('is-selected', state.selection.has(widget.id))
      canvas.insertBefore(item.el, gridLayer) // l'ordre du document est l'ordre d'empilement
    })
  }

  function renderChrome(): void {
    const doc = state.doc
    canvas.style.width = `${doc.canvas.width}px`
    canvas.style.height = `${doc.canvas.height}px`
    canvas.style.background = doc.canvas.background
    canvas.style.backgroundImage = doc.canvas.bg_image && /^data:image\//.test(doc.canvas.bg_image) ? `url("${doc.canvas.bg_image}")` : ''
    canvas.style.backgroundSize = '100% 100%'
    canvas.style.transform = `scale(${zoom})`
    stage.style.width = `${doc.canvas.width * zoom}px`
    stage.style.height = `${doc.canvas.height * zoom}px`
    gridLayer.hidden = !showGrid
    gridLayer.style.backgroundSize = `${state.grid}px ${state.grid}px`
    gridLayer.classList.toggle('on-light', !isDarkColor(doc.canvas.background))
    zoomLabel.textContent = `${Math.round(zoom * 100)} %`
    if (document.activeElement !== nameInput) nameInput.value = doc.name
    undoButton.disabled = !state.canUndo || previewing
    redoButton.disabled = !state.canRedo || previewing
    saveButton.disabled = previewing
    snapBox.checked = state.snapping
    renderSelection()
  }

  function renderSelection(): void {
    selectionLayer.replaceChildren()
    const selected = state.selected
    if (selected.length === 0 || previewing) return
    const left = Math.min(...selected.map((w) => w.x))
    const top = Math.min(...selected.map((w) => w.y))
    const right = Math.max(...selected.map((w) => w.x + w.w))
    const bottom = Math.max(...selected.map((w) => w.y + w.h))
    const box = element('div', 'ed-selbox')
    Object.assign(box.style, { left: `${left}px`, top: `${top}px`, width: `${right - left}px`, height: `${bottom - top}px` })
    if (selected.length === 1) {
      for (const handle of HANDLES) {
        const knob = element('div', `ed-handle ed-handle--${handle}`)
        knob.dataset.handle = handle
        box.append(knob)
      }
    }
    selectionLayer.append(box)
  }

  const unsubscribe = state.onChange(() => {
    if (destroyed) return
    syncItems()
    renderChrome()
    setStatus()
  })

  // -- zoom --------------------------------------------------------------------------------
  function setZoom(value: number): void {
    zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round(value * 100) / 100))
    renderChrome()
  }
  function fitToViewport(): void {
    const width = viewport.clientWidth
    const height = viewport.clientHeight
    if (width <= 0 || height <= 0) return
    setZoom(Math.min((width - 32) / state.doc.canvas.width, (height - 32) / state.doc.canvas.height, 1))
  }

  // -- gestes (glisser, redimensionner, sélection par zone) ---------------------------------
  function toCanvas(event: { clientX: number; clientY: number }): { x: number; y: number } {
    const rect = canvas.getBoundingClientRect()
    return { x: (event.clientX - rect.left) / zoom, y: (event.clientY - rect.top) / zoom }
  }

  function track(event: PointerEvent, move: (event: PointerEvent) => void, finish: (event: PointerEvent) => void): void {
    const target = event.target as Element
    target.setPointerCapture?.(event.pointerId)
    const onMove = (e: PointerEvent) => move(e)
    const onUp = (e: PointerEvent) => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      finish(e)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }

  canvas.addEventListener('pointerdown', (event) => {
    if (previewing || event.button !== 0) return
    viewport.focus({ preventScroll: true })
    const target = event.target as HTMLElement
    const start = toCanvas(event)

    const handleEl = target.closest<HTMLElement>('[data-handle]')
    if (handleEl && state.selected.length === 1) {
      const widget = state.selected[0] as WidgetDef
      const origin = { x: widget.x, y: widget.y, w: widget.w, h: widget.h }
      const handle = handleEl.dataset.handle as Handle
      event.preventDefault()
      state.begin()
      track(event, (e) => {
        const now = toCanvas(e)
        const grid = state.snapping && !e.altKey ? state.grid : 0
        state.setGeometry(widget.id, resizeRect(origin, handle, now.x - start.x, now.y - start.y, grid))
      }, () => state.end())
      return
    }

    const itemEl = target.closest<HTMLElement>('.ed-item')
    if (itemEl) {
      const id = itemEl.dataset.widgetId as string
      event.preventDefault()
      if (event.shiftKey || event.ctrlKey || event.metaKey) {
        state.toggle(id)
        return
      }
      if (!state.selection.has(id)) state.select([id])
      const origins = new Map(state.selected.map((w) => [w.id, { x: w.x, y: w.y }]))
      let moved = false
      track(event, (e) => {
        const now = toCanvas(e)
        if (!moved && Math.hypot(now.x - start.x, now.y - start.y) * zoom < DRAG_THRESHOLD_PX) return
        if (!moved) {
          moved = true
          state.begin()
        }
        state.placeSelected(origins, now.x - start.x, now.y - start.y, !e.altKey)
      }, () => state.end())
      return
    }

    // Fond du canevas : rectangle de sélection.
    event.preventDefault()
    const additive = event.shiftKey
    band.hidden = false
    track(event, (e) => {
      const now = toCanvas(e)
      const r = rectBetween(start.x, start.y, now.x, now.y)
      Object.assign(band.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` })
    }, (e) => {
      band.hidden = true
      const now = toCanvas(e)
      const area = rectBetween(start.x, start.y, now.x, now.y)
      if (area.w * zoom < DRAG_THRESHOLD_PX && area.h * zoom < DRAG_THRESHOLD_PX) {
        if (!additive) state.clearSelection()
      } else {
        state.selectInRect(area, additive)
      }
    })
  })

  // -- clavier -----------------------------------------------------------------------------
  const onKeyDown = (event: KeyboardEvent) => {
    if (destroyed || document.querySelector('.ed-modal, .syn-confirm')) return
    const target = event.target
    if (target instanceof Element && target.closest('input, textarea, select, [contenteditable="true"]')) return
    const mod = event.ctrlKey || event.metaKey
    const key = event.key.toLowerCase()
    if (previewing) {
      if (key === 'escape') setPreview(false)
      return
    }
    const handled = (): void => event.preventDefault()
    if (mod && key === 's') return handled(), void save()
    if (mod && key === 'z') return handled(), event.shiftKey ? state.redo() : state.undo()
    if (mod && key === 'y') return handled(), state.redo()
    if (mod && key === 'a') return handled(), state.selectAll()
    if (mod && key === 'c') return handled(), state.copy()
    if (mod && key === 'x') return handled(), state.cut()
    if (mod && key === 'v') return handled(), void state.paste()
    if (mod && key === 'd') return handled(), void state.duplicate()
    if (key === 'delete' || key === 'backspace') return handled(), state.deleteSelected()
    if (key === 'escape') return state.clearSelection()
    const step = event.shiftKey ? 10 : 1
    const arrows: Record<string, [number, number]> = { arrowleft: [-step, 0], arrowright: [step, 0], arrowup: [0, -step], arrowdown: [0, step] }
    const move = arrows[key]
    if (move && state.selection.size > 0) {
      handled()
      state.mutate(() => state.selected.forEach((w) => ((w.x += move[0]), (w.y += move[1]))), `nudge:${[...state.selection].join(',')}`)
    }
  }
  document.addEventListener('keydown', onKeyDown)

  // -- aperçu en direct --------------------------------------------------------------------
  function setPreview(on: boolean): void {
    previewing = on
    previewButton.setAttribute('aria-pressed', String(on))
    previewButton.textContent = on ? 'Retour à l’édition' : 'Aperçu en direct'
    preview.hidden = !on
    stage.hidden = on
    palette.classList.toggle('is-disabled', on)
    previewViewer?.destroy()
    previewViewer = null
    if (on) {
      const doc: SynopticDoc = cloneDoc(state.doc)
      previewViewer = mountViewer(
        preview,
        { id: record?.id ?? 'aperçu', name: doc.name, slug: record?.slug ?? 'apercu', version: record?.version ?? 0, updated_at: new Date().toISOString(), doc },
        { api: deps.api, live: deps.live, role: deps.role, navigate: () => setStatus('La navigation est désactivée en aperçu.'), confirm },
      )
    }
    renderChrome()
  }

  // -- enregistrement ----------------------------------------------------------------------
  let statusMessage = ''
  function setStatus(message?: string): void {
    if (message !== undefined) statusMessage = message
    const problems = documentProblems(state.doc)
    const parts = [statusMessage]
    if (state.dirty) parts.push('Modifications non enregistrées')
    else if (record) parts.push(`Version ${record.version} enregistrée`)
    if (problems.length > 0) parts.push(`${problems.length} avertissement${problems.length > 1 ? 's' : ''} : ${problems[0]?.message}`)
    statusBar.textContent = parts.filter(Boolean).join(' — ')
    saveButton.dataset.dirty = String(state.dirty)
  }

  async function save(): Promise<void> {
    if (destroyed || previewing) return
    saveButton.disabled = true
    setStatus('Enregistrement…')
    try {
      const doc = cloneDoc(state.doc)
      const saved = record
        ? await deps.api.saveSynoptic(record.id, doc, record.version)
        : await deps.api.createSynoptic(doc)
      record = saved
      state.markSaved()
      statusMessage = ''
      setStatus()
      deps.onSaved?.(saved)
    } catch (error) {
      const detail = error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error)
      setStatus(error instanceof ApiError && error.status === 409 ? `Conflit : ${detail}` : `Échec de l'enregistrement : ${detail}`)
    } finally {
      saveButton.disabled = previewing
    }
  }

  function openVersionsDialog(): void {
    if (!record) {
      setStatus('Enregistrez d’abord le synoptique pour disposer de versions.')
      return
    }
    openVersions({
      api: deps.api,
      record,
      confirm,
      onRestored: (restored) => {
        record = restored
        state.load(restored.doc)
        setStatus(`Version ${restored.version} créée par restauration.`)
        deps.onSaved?.(restored)
      },
    })
  }

  async function leave(): Promise<void> {
    if (state.dirty && !(await confirm('Des modifications ne sont pas enregistrées. Quitter quand même ?'))) return
    deps.exit()
  }

  const beforeUnload = (event: BeforeUnloadEvent) => {
    if (state.dirty) event.preventDefault()
  }
  window.addEventListener('beforeunload', beforeUnload)

  // -- démarrage ---------------------------------------------------------------------------
  syncItems()
  renderChrome()
  setStatus()
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => fitToViewport())

  return {
    state,
    save,
    destroy() {
      destroyed = true
      unsubscribe()
      document.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('beforeunload', beforeUnload)
      previewViewer?.destroy()
      for (const item of items.values()) item.instance.destroy()
      items.clear()
      root.remove()
      void panel
    },
  }
}
