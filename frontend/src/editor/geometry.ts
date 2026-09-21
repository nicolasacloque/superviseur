/** Géométrie de l'éditeur : magnétisme, alignement, redimensionnement (fonctions pures). */

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export const MIN_SIZE = 8

export function snap(value: number, grid: number): number {
  return grid > 0 ? Math.round(value / grid) * grid : value
}

export function snapRect(rect: Rect, grid: number): Rect {
  return { x: snap(rect.x, grid), y: snap(rect.y, grid), w: Math.max(MIN_SIZE, snap(rect.w, grid)), h: Math.max(MIN_SIZE, snap(rect.h, grid)) }
}

export function boundsOf(rects: Rect[]): Rect | null {
  if (rects.length === 0) return null
  const left = Math.min(...rects.map((r) => r.x))
  const top = Math.min(...rects.map((r) => r.y))
  const right = Math.max(...rects.map((r) => r.x + r.w))
  const bottom = Math.max(...rects.map((r) => r.y + r.h))
  return { x: left, y: top, w: right - left, h: bottom - top }
}

export function intersects(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
}

export function contains(rect: Rect, x: number, y: number): boolean {
  return x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h
}

/** Rectangle normalisé entre deux points (glisser dans n'importe quel sens). */
export function rectBetween(x1: number, y1: number, x2: number, y2: number): Rect {
  return { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1) }
}

export type AlignMode = 'left' | 'center' | 'right' | 'top' | 'middle' | 'bottom'

/** Aligne des rectangles sur leur boîte englobante ; retourne les nouvelles positions par index. */
export function alignRects(rects: Rect[], mode: AlignMode): Rect[] {
  const box = boundsOf(rects)
  if (!box || rects.length < 2) return rects.map((r) => ({ ...r }))
  return rects.map((r) => {
    switch (mode) {
      case 'left':
        return { ...r, x: box.x }
      case 'center':
        return { ...r, x: box.x + (box.w - r.w) / 2 }
      case 'right':
        return { ...r, x: box.x + box.w - r.w }
      case 'top':
        return { ...r, y: box.y }
      case 'middle':
        return { ...r, y: box.y + (box.h - r.h) / 2 }
      case 'bottom':
        return { ...r, y: box.y + box.h - r.h }
    }
  })
}

/** Répartit des rectangles à intervalles réguliers entre le premier et le dernier (3 minimum). */
export function distributeRects(rects: Rect[], axis: 'horizontal' | 'vertical'): Rect[] {
  if (rects.length < 3) return rects.map((r) => ({ ...r }))
  const horizontal = axis === 'horizontal'
  const order = rects.map((_, i) => i).sort((a, b) => (horizontal ? rects[a]!.x - rects[b]!.x : rects[a]!.y - rects[b]!.y))
  const first = rects[order[0]!]!
  const last = rects[order[order.length - 1]!]!
  const start = horizontal ? first.x : first.y
  const end = horizontal ? last.x + last.w : last.y + last.h
  const total = order.reduce((sum, i) => sum + (horizontal ? rects[i]!.w : rects[i]!.h), 0)
  const gap = (end - start - total) / (rects.length - 1)
  const result = rects.map((r) => ({ ...r }))
  let cursor = start
  for (const i of order) {
    const r = result[i]!
    if (horizontal) {
      r.x = cursor
      cursor += r.w + gap
    } else {
      r.y = cursor
      cursor += r.h + gap
    }
  }
  return result
}

export type Handle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w'
export const HANDLES: Handle[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']

/** Nouveau rectangle après avoir tiré la poignée `handle` de (dx, dy), avec magnétisme et taille minimale. */
export function resizeRect(rect: Rect, handle: Handle, dx: number, dy: number, grid = 0): Rect {
  let left = rect.x
  let top = rect.y
  let right = rect.x + rect.w
  let bottom = rect.y + rect.h
  if (handle.includes('w')) left = snap(left + dx, grid)
  if (handle.includes('e')) right = snap(right + dx, grid)
  if (handle.includes('n')) top = snap(top + dy, grid)
  if (handle.includes('s')) bottom = snap(bottom + dy, grid)
  // La poignée ne peut pas dépasser le bord opposé : la taille minimale est conservée.
  if (right - left < MIN_SIZE) {
    if (handle.includes('w')) left = right - MIN_SIZE
    else right = left + MIN_SIZE
  }
  if (bottom - top < MIN_SIZE) {
    if (handle.includes('n')) top = bottom - MIN_SIZE
    else bottom = top + MIN_SIZE
  }
  return { x: left, y: top, w: right - left, h: bottom - top }
}
