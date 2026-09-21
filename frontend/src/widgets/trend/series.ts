/** Données d'une courbe : fonctions pures (testables sans DOM ni canvas). */

import type { HistoryItem } from '../../api/types'

export type Range = '15m' | '1h' | '6h' | '24h' | '7d' | '30d'

export const RANGES: Range[] = ['15m', '1h', '6h', '24h', '7d', '30d']

export const RANGE_SECONDS: Record<Range, number> = {
  '15m': 900,
  '1h': 3600,
  '6h': 21600,
  '24h': 86400,
  '7d': 604800,
  '30d': 2592000,
}

export interface SeriesData {
  /** Instants en secondes depuis l'epoch, strictement croissants. */
  t: number[]
  v: (number | null)[]
  /** Index du premier point arrivé en temps réel (les précédents viennent de l'historique). */
  liveFrom: number
}

/** Format attendu par uPlot : [instants, série 1, série 2...] sur les mêmes instants. */
export type Aligned = [number[], ...(number | null)[][]]

export function emptySeries(): SeriesData {
  return { t: [], v: [], liveFrom: 0 }
}

/** Convertit la réponse de l'historique ; les instants invalides sont ignorés. */
export function historyToSeries(items: HistoryItem[]): SeriesData {
  const points = new Map<number, number | null>()
  for (const item of items) {
    const t = Date.parse(item.ts) / 1000
    if (Number.isFinite(t)) points.set(t, item.value ?? null)
  }
  const times = [...points.keys()].sort((a, b) => a - b)
  return { t: times, v: times.map((t) => points.get(t) ?? null), liveFrom: times.length }
}

/** Aligne plusieurs séries sur l'union de leurs instants ; les trous valent `null`. */
export function alignSeries(series: SeriesData[]): Aligned {
  const times = [...new Set(series.flatMap((s) => s.t))].sort((a, b) => a - b)
  const columns = series.map((s) => {
    const column: (number | null)[] = new Array(times.length).fill(null)
    let cursor = 0
    for (let i = 0; i < times.length && cursor < s.t.length; i++) {
      if (times[i] === s.t[cursor]) column[i] = s.v[cursor++] ?? null
    }
    return column
  })
  return [times, ...columns]
}

/**
 * Ajoute une valeur temps réel. Un instant déjà présent est remplacé, un instant plus ancien que
 * la dernière valeur est ignoré. Si `minGap` secondes ne se sont pas écoulées depuis la dernière
 * valeur temps réel, elle est mise à jour en place : le nombre de points reste borné sur les
 * longues périodes sans déplacer les points issus de l'historique.
 */
export function appendLive(series: SeriesData, t: number, value: number | null, minGap: number): void {
  const last = series.t.length - 1
  if (last >= 0 && t < (series.t[last] ?? -Infinity)) return
  if (last >= 0 && t === series.t[last]) {
    series.v[last] = value
    return
  }
  if (last >= series.liveFrom && t - (series.t[last] ?? -Infinity) < minGap) {
    series.t[last] = t
    series.v[last] = value
    return
  }
  series.t.push(t)
  series.v.push(value)
}

/** Retire les points antérieurs à `minT` (fenêtre glissante). */
export function trimBefore(series: SeriesData, minT: number): void {
  let cut = 0
  while (cut < series.t.length && (series.t[cut] ?? Infinity) < minT) cut++
  if (cut === 0) return
  series.t.splice(0, cut)
  series.v.splice(0, cut)
  series.liveFrom = Math.max(0, series.liveFrom - cut)
}

export function lastPoint(series: SeriesData): { t: number; v: number | null } | null {
  const last = series.t.length - 1
  return last < 0 ? null : { t: series.t[last] ?? 0, v: series.v[last] ?? null }
}

/** Nombre de points à demander au serveur : environ un point tous les 2 pixels. */
export function maxPointsFor(widthPx: number): number {
  return Math.min(1500, Math.max(100, Math.round(widthPx / 2)))
}

const formatters = new Map<number, Intl.NumberFormat>()

/** `21,4 °C` ; `—` sans valeur. Peu de décimales : la lecture prime sur la précision. */
export function formatValue(value: number | null, unit?: string | null): string {
  if (value === null || !Number.isFinite(value)) return '—'
  const decimals = Math.abs(value) >= 1000 ? 0 : Math.abs(value) >= 100 ? 1 : 2
  let formatter = formatters.get(decimals)
  if (!formatter) {
    formatter = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: decimals })
    formatters.set(decimals, formatter)
  }
  return unit ? `${formatter.format(value)} ${unit}` : formatter.format(value)
}
