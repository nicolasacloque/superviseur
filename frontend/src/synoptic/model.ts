/** Modèle du document d'un synoptique (schema 1, section 10.2 du cahier des charges). */

export const WIDGET_TYPES = [
  'value',
  'label',
  'gauge',
  'indicator',
  'switch',
  'setpoint',
  'trend',
  'alarm_list',
  'image',
  'link',
  'shape',
] as const

export type WidgetType = (typeof WIDGET_TYPES)[number]

export type StyleMap = Record<string, string | number>

export interface Rule {
  when: string
  style: StyleMap
}

/** Un widget : géométrie, liaison aux points, style, règles, et propriétés propres à son type. */
export interface WidgetDef {
  id: string
  type: WidgetType
  x: number
  y: number
  w: number
  h: number
  bind: Record<string, unknown>
  style: StyleMap
  rules: Rule[]
  [property: string]: unknown
}

export interface Canvas {
  width: number
  height: number
  background: string
  bg_image: string | null
}

export interface LinkDef {
  type: 'navigate'
  widget: string
  target: string
}

export interface SynopticDoc {
  schema: 1
  name: string
  canvas: Canvas
  widgets: WidgetDef[]
  links: LinkDef[]
}

export const MAX_WIDGETS = 500
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024

export function newDoc(name = 'Nouveau synoptique'): SynopticDoc {
  return {
    schema: 1,
    name,
    canvas: { width: 1920, height: 1080, background: '#0f172a', bg_image: null },
    widgets: [],
    links: [],
  }
}

export function cloneDoc<T>(doc: T): T {
  return JSON.parse(JSON.stringify(doc)) as T
}

/** Identifiants de points liés à un widget (`bind.point` et `bind.points[].point`). */
export function boundPoints(widget: WidgetDef): string[] {
  const found: string[] = []
  const single = widget.bind?.point
  if (typeof single === 'string' && single) found.push(single)
  const many = widget.bind?.points
  if (Array.isArray(many)) {
    for (const entry of many) {
      const id = typeof entry === 'string' ? entry : (entry as { point?: unknown })?.point
      if (typeof id === 'string' && id) found.push(id)
    }
  }
  return found
}

export function allBoundPoints(doc: SynopticDoc): string[] {
  return [...new Set(doc.widgets.flatMap(boundPoints))]
}

/** Prochain identifiant libre : `w1`, `w2`... */
export function nextWidgetId(doc: SynopticDoc): string {
  const used = new Set(doc.widgets.map((w) => w.id))
  let n = doc.widgets.length + 1
  while (used.has(`w${n}`)) n++
  return `w${n}`
}

/** Luminance relative (0 = noir, 1 = blanc) d'une couleur CSS `#rgb`, `#rrggbb` ou `rgb()`. */
export function luminance(color: string): number {
  let r = 0
  let g = 0
  let b = 0
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color.trim())
  if (hex) {
    const digits = (hex[1] as string).length === 3 ? [...(hex[1] as string)].map((d) => d + d).join('') : (hex[1] as string)
    ;[r, g, b] = [0, 2, 4].map((i) => parseInt(digits.slice(i, i + 2), 16))
  } else {
    const rgb = /^rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/i.exec(color.trim())
    if (rgb) [r, g, b] = [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])]
    else return 0.5
  }
  const channel = (v: number) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

export function isDarkColor(color: string): boolean {
  return luminance(color) < 0.4
}

export interface DocProblem {
  widget?: string
  message: string
}

/** Vérifications rapides avant enregistrement ; l'API reste l'autorité. */
export function documentProblems(doc: SynopticDoc): DocProblem[] {
  const problems: DocProblem[] = []
  if (!doc.name.trim()) problems.push({ message: 'Le synoptique doit avoir un nom.' })
  if (doc.widgets.length > MAX_WIDGETS) problems.push({ message: `${MAX_WIDGETS} widgets au maximum.` })
  const needsPoint: Partial<Record<WidgetType, string>> = {
    value: 'point',
    gauge: 'point',
    indicator: 'point',
    switch: 'point',
    setpoint: 'point',
    trend: 'points',
  }
  for (const widget of doc.widgets) {
    const kind = needsPoint[widget.type]
    if (kind && boundPoints(widget).length === 0) {
      problems.push({ widget: widget.id, message: `Le widget ${widget.id} (${widget.type}) n'est lié à aucun point.` })
    }
    if (widget.type === 'link' && typeof widget.target !== 'string') {
      problems.push({ widget: widget.id, message: `Le lien ${widget.id} n'a pas de cible.` })
    }
  }
  return problems
}
