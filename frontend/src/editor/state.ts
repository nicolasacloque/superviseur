/** État de l'éditeur : document, sélection, presse-papiers, annuler/rétablir. */

import { cloneDoc, nextWidgetId, type SynopticDoc, type WidgetDef, type WidgetType, newDoc } from '../synoptic/model'
import { MODULES } from '../widgets/registry'
import { type AlignMode, alignRects, distributeRects, type Rect, rectBetween, intersects, snap } from './geometry'

export const HISTORY_LIMIT = 100
/** Deux modifications de la même propriété à moins de ce délai forment une seule étape d'annulation. */
export const COALESCE_MS = 800
export const PASTE_OFFSET = 20

export class EditorState {
  doc: SynopticDoc
  selection = new Set<string>()
  grid = 10
  snapping = true
  private clipboard: WidgetDef[] = []
  private undoStack: string[] = []
  private redoStack: string[] = []
  private saved: string
  private gesture: { before: string } | null = null
  private lastCoalesce: { key: string; at: number } | null = null
  private readonly listeners = new Set<() => void>()

  constructor(doc: SynopticDoc = newDoc(), private readonly now: () => number = () => Date.now()) {
    this.doc = cloneDoc(doc)
    this.saved = this.snapshot()
  }

  // -- observation -------------------------------------------------------------------------
  onChange(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private emit(): void {
    for (const listener of this.listeners) listener()
  }

  private snapshot(): string {
    return JSON.stringify(this.doc)
  }

  get dirty(): boolean {
    return this.snapshot() !== this.saved
  }

  get canUndo(): boolean {
    return this.undoStack.length > 0
  }

  get canRedo(): boolean {
    return this.redoStack.length > 0
  }

  get canPaste(): boolean {
    return this.clipboard.length > 0
  }

  get selected(): WidgetDef[] {
    return this.doc.widgets.filter((w) => this.selection.has(w.id))
  }

  widget(id: string): WidgetDef | undefined {
    return this.doc.widgets.find((w) => w.id === id)
  }

  markSaved(): void {
    this.saved = this.snapshot()
    this.emit()
  }

  /** Remplace le document (chargement, restauration) : l'historique repart de zéro. */
  load(doc: SynopticDoc): void {
    this.doc = cloneDoc(doc)
    this.selection.clear()
    this.undoStack = []
    this.redoStack = []
    this.gesture = null
    this.lastCoalesce = null
    this.saved = this.snapshot()
    this.emit()
  }

  // -- historique --------------------------------------------------------------------------
  /**
   * Applique une modification et l'enregistre comme une étape d'annulation. `coalesce` fusionne les
   * modifications successives d'une même propriété (saisie au clavier) en une seule étape.
   */
  mutate(change: () => void, coalesce?: string): void {
    const before = this.snapshot()
    change()
    if (this.snapshot() === before) return
    if (this.gesture === null) {
      const same = coalesce && this.lastCoalesce?.key === coalesce && this.now() - this.lastCoalesce.at < COALESCE_MS
      if (!same) this.push(before)
      this.lastCoalesce = coalesce ? { key: coalesce, at: this.now() } : null
    }
    this.redoStack = []
    this.emit()
  }

  private push(before: string): void {
    this.undoStack.push(before)
    if (this.undoStack.length > HISTORY_LIMIT) this.undoStack.shift()
  }

  /** Début d'un geste continu (glisser, redimensionner) : une seule étape d'annulation à la fin. */
  begin(): void {
    if (this.gesture === null) this.gesture = { before: this.snapshot() }
  }

  end(): void {
    if (this.gesture === null) return
    const { before } = this.gesture
    this.gesture = null
    if (this.snapshot() !== before) this.push(before)
    this.lastCoalesce = null
    this.emit()
  }

  undo(): void {
    const previous = this.undoStack.pop()
    if (previous === undefined) return
    this.redoStack.push(this.snapshot())
    this.restore(previous)
  }

  redo(): void {
    const next = this.redoStack.pop()
    if (next === undefined) return
    this.undoStack.push(this.snapshot())
    this.restore(next)
  }

  private restore(snapshot: string): void {
    this.doc = JSON.parse(snapshot) as SynopticDoc
    const existing = new Set(this.doc.widgets.map((w) => w.id))
    for (const id of [...this.selection]) if (!existing.has(id)) this.selection.delete(id)
    this.lastCoalesce = null
    this.emit()
  }

  // -- sélection ---------------------------------------------------------------------------
  select(ids: string[], additive = false): void {
    if (!additive) this.selection.clear()
    for (const id of ids) if (this.widget(id)) this.selection.add(id)
    this.emit()
  }

  toggle(id: string): void {
    if (this.selection.has(id)) this.selection.delete(id)
    else if (this.widget(id)) this.selection.add(id)
    this.emit()
  }

  selectAll(): void {
    this.selection = new Set(this.doc.widgets.map((w) => w.id))
    this.emit()
  }

  clearSelection(): void {
    if (this.selection.size === 0) return
    this.selection.clear()
    this.emit()
  }

  /** Sélectionne les widgets qui touchent la zone (rectangle de sélection). */
  selectInRect(area: Rect, additive = false): void {
    const hit = this.doc.widgets.filter((w) => intersects(area, w)).map((w) => w.id)
    this.select(hit, additive)
  }

  // -- widgets -----------------------------------------------------------------------------
  addWidget(type: WidgetType, at?: { x: number; y: number }): WidgetDef {
    const defaults = cloneDoc(MODULES[type].defaults()) as Omit<WidgetDef, 'id'>
    const id = nextWidgetId(this.doc)
    const widget = { ...defaults, id, type } as WidgetDef
    const stagger = (this.doc.widgets.length % 10) * this.grid
    // Le centre du widget tombe sur le point demandé ; à défaut, un coin décalé pour ne pas empiler.
    widget.x = this.fit(at ? at.x - widget.w / 2 : 40 + stagger)
    widget.y = this.fit(at ? at.y - widget.h / 2 : 40 + stagger)
    this.mutate(() => {
      this.doc.widgets.push(widget)
      this.selection = new Set([id])
    })
    return widget
  }

  private fit(value: number): number {
    return this.snapping ? snap(value, this.grid) : Math.round(value)
  }

  deleteSelected(): void {
    if (this.selection.size === 0) return
    this.mutate(() => {
      const gone = this.selection
      this.doc.widgets = this.doc.widgets.filter((w) => !gone.has(w.id))
      this.doc.links = this.doc.links.filter((l) => !gone.has(l.widget))
      this.selection = new Set()
    })
  }

  /** Déplace la sélection ; le magnétisme cale le coin haut-gauche de la sélection sur la grille. */
  moveSelected(dx: number, dy: number, snapToGrid = false): void {
    const targets = this.selected
    if (targets.length === 0) return
    this.mutate(() => {
      const origin = targets.reduce((box, w) => ({ x: Math.min(box.x, w.x), y: Math.min(box.y, w.y) }), { x: Infinity, y: Infinity })
      let mx = dx
      let my = dy
      if (snapToGrid && this.snapping) {
        mx = snap(origin.x + dx, this.grid) - origin.x
        my = snap(origin.y + dy, this.grid) - origin.y
      }
      for (const w of targets) {
        w.x += mx
        w.y += my
      }
    })
  }

  /**
   * Place la sélection à `origines + (dx, dy)` : positions absolues, donc sans dérive pendant un
   * glisser. Le magnétisme cale le coin haut-gauche de la sélection, pas chaque widget.
   */
  placeSelected(origins: Map<string, { x: number; y: number }>, dx: number, dy: number, snapToGrid: boolean): void {
    const targets = this.selected.filter((w) => origins.has(w.id))
    if (targets.length === 0) return
    this.mutate(() => {
      const corner = [...origins.values()].reduce((box, o) => ({ x: Math.min(box.x, o.x), y: Math.min(box.y, o.y) }), { x: Infinity, y: Infinity })
      let mx = dx
      let my = dy
      if (snapToGrid && this.snapping) {
        mx = snap(corner.x + dx, this.grid) - corner.x
        my = snap(corner.y + dy, this.grid) - corner.y
      }
      for (const w of targets) {
        const origin = origins.get(w.id)!
        w.x = origin.x + mx
        w.y = origin.y + my
      }
    })
  }

  /** Position et taille d'un widget (poignées, champs du panneau). */
  setGeometry(id: string, rect: Partial<Rect>): void {
    const widget = this.widget(id)
    if (!widget) return
    this.mutate(() => Object.assign(widget, rect), `geometry:${id}`)
  }

  alignSelected(mode: AlignMode): void {
    const targets = this.selected
    if (targets.length < 2) return
    this.mutate(() => {
      const aligned = alignRects(targets, mode)
      targets.forEach((w, i) => Object.assign(w, { x: aligned[i]!.x, y: aligned[i]!.y }))
    })
  }

  distributeSelected(axis: 'horizontal' | 'vertical'): void {
    const targets = this.selected
    if (targets.length < 3) return
    this.mutate(() => {
      const placed = distributeRects(targets, axis)
      targets.forEach((w, i) => Object.assign(w, { x: placed[i]!.x, y: placed[i]!.y }))
    })
  }

  /** Ordre d'empilement : le dernier de la liste est dessiné au-dessus. */
  bringToFront(): void {
    const ids = this.selection
    this.mutate(() => {
      const chosen = this.doc.widgets.filter((w) => ids.has(w.id))
      this.doc.widgets = [...this.doc.widgets.filter((w) => !ids.has(w.id)), ...chosen]
    })
  }

  sendToBack(): void {
    const ids = this.selection
    this.mutate(() => {
      const chosen = this.doc.widgets.filter((w) => ids.has(w.id))
      this.doc.widgets = [...chosen, ...this.doc.widgets.filter((w) => !ids.has(w.id))]
    })
  }

  // -- presse-papiers ----------------------------------------------------------------------
  copy(): void {
    this.clipboard = cloneDoc(this.selected)
  }

  cut(): void {
    this.copy()
    this.deleteSelected()
  }

  /** Colle avec de nouveaux identifiants, décalés pour rester visibles ; les liens sont recopiés. */
  paste(): WidgetDef[] {
    if (this.clipboard.length === 0) return []
    const created: WidgetDef[] = []
    this.mutate(() => {
      const links = new Map(this.doc.links.map((l) => [l.widget, l.target]))
      this.selection = new Set()
      for (const original of this.clipboard) {
        const copy = cloneDoc(original)
        const id = nextWidgetId(this.doc)
        copy.x += PASTE_OFFSET
        copy.y += PASTE_OFFSET
        this.doc.widgets.push({ ...copy, id })
        this.selection.add(id)
        created.push(this.doc.widgets.at(-1)!)
        const target = links.get(original.id)
        if (target) this.doc.links.push({ type: 'navigate', widget: id, target })
      }
    })
    // Deux collages successifs ne s'empilent pas : le presse-papiers avance avec la copie.
    this.clipboard = cloneDoc(created)
    return created
  }

  duplicate(): WidgetDef[] {
    this.copy()
    return this.paste()
  }

  // -- propriétés --------------------------------------------------------------------------
  /** Fixe une propriété par chemin (`bind.point`, `style.color`, `text`) ; `undefined` la supprime. */
  setProperty(id: string, path: string, value: unknown): void {
    const widget = this.widget(id)
    if (!widget) return
    this.mutate(() => {
      const keys = path.split('.')
      let holder: Record<string, unknown> = widget
      for (const key of keys.slice(0, -1)) {
        if (typeof holder[key] !== 'object' || holder[key] === null) holder[key] = {}
        holder = holder[key] as Record<string, unknown>
      }
      const last = keys.at(-1) as string
      if (value === undefined || value === '') delete holder[last]
      else holder[last] = value
    }, `prop:${id}:${path}`)
  }

  setRules(id: string, rules: WidgetDef['rules']): void {
    const widget = this.widget(id)
    if (widget) this.mutate(() => (widget.rules = rules), `rules:${id}`)
  }

  setLink(id: string, target: string | null): void {
    this.mutate(() => {
      this.doc.links = this.doc.links.filter((l) => l.widget !== id)
      if (target) this.doc.links.push({ type: 'navigate', widget: id, target })
    })
  }

  linkOf(id: string): string | null {
    return this.doc.links.find((l) => l.widget === id)?.target ?? null
  }

  setName(name: string): void {
    this.mutate(() => (this.doc.name = name), 'name')
  }

  setCanvas(patch: Partial<SynopticDoc['canvas']>): void {
    this.mutate(() => Object.assign(this.doc.canvas, patch), `canvas:${Object.keys(patch).join(',')}`)
  }

  setGrid(grid: number, snapping: boolean): void {
    this.grid = Math.max(1, Math.round(grid))
    this.snapping = snapping
    this.emit()
  }

  /** Rectangle de sélection à l'écran -> zone du canevas. */
  static area(x1: number, y1: number, x2: number, y2: number): Rect {
    return rectBetween(x1, y1, x2, y2)
  }
}
