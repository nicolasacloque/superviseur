import type { EditorField } from '../types'
import { RANGES, type Range } from './series'
import type { ThemePreference } from './theme'

export const MAX_SERIES = 8

export interface TrendSeriesConfig {
  point: string
  /** Libellé de la légende ; par défaut le nom du point. */
  label?: string
}

/** Configuration d'un widget `trend` telle qu'enregistrée dans le JSON du synoptique. */
export interface TrendConfig {
  bind: { points: TrendSeriesConfig[] }
  range: Range
  live: boolean
  yMin?: number
  yMax?: number
  rangeSelector: boolean
  theme: ThemePreference
}

export class TrendConfigError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'TrendConfigError'
  }
}

/** Valide la configuration (venue d'un JSON éditable) et applique les valeurs par défaut. */
export function normalizeConfig(raw: unknown): TrendConfig {
  const config = (raw ?? {}) as Record<string, unknown>
  const bind = (config.bind ?? {}) as { points?: unknown }
  if (!Array.isArray(bind.points) || bind.points.length === 0) {
    throw new TrendConfigError('trend : au moins un point est requis (bind.points)')
  }
  if (bind.points.length > MAX_SERIES) {
    throw new TrendConfigError(`trend : ${MAX_SERIES} points au maximum (reçu ${bind.points.length})`)
  }
  const points = bind.points.map((entry, index): TrendSeriesConfig => {
    const item = (typeof entry === 'string' ? { point: entry } : entry) as Record<string, unknown>
    if (typeof item?.point !== 'string' || item.point === '') {
      throw new TrendConfigError(`trend : identifiant de point manquant (bind.points[${index}])`)
    }
    return { point: item.point, label: typeof item.label === 'string' ? item.label : undefined }
  })
  if (new Set(points.map((p) => p.point)).size !== points.length) {
    throw new TrendConfigError('trend : un point ne peut apparaître qu\'une fois')
  }
  const range = config.range ?? '1h'
  if (!RANGES.includes(range as Range)) {
    throw new TrendConfigError(`trend : période inconnue « ${String(range)} » (${RANGES.join(', ')})`)
  }
  const theme = config.theme ?? 'auto'
  if (theme !== 'auto' && theme !== 'light' && theme !== 'dark') {
    throw new TrendConfigError(`trend : thème inconnu « ${String(theme)} »`)
  }
  const number = (key: 'yMin' | 'yMax'): number | undefined => {
    const value = config[key]
    if (value === undefined || value === null || value === '') return undefined
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      throw new TrendConfigError(`trend : ${key} doit être un nombre`)
    }
    return value
  }
  const yMin = number('yMin')
  const yMax = number('yMax')
  if (yMin !== undefined && yMax !== undefined && yMin >= yMax) {
    throw new TrendConfigError('trend : yMin doit être inférieur à yMax')
  }
  return {
    bind: { points },
    range: range as Range,
    live: config.live !== false,
    yMin,
    yMax,
    rangeSelector: config.rangeSelector !== false,
    theme,
  }
}

/** Propriétés éditables : l'éditeur (Jalon 6) génère son panneau depuis cette description. */
export const editorSchema: EditorField[] = [
  { key: 'bind.points', label: 'Points', type: 'points', min: 1, max: MAX_SERIES },
  {
    key: 'range',
    label: 'Période affichée',
    type: 'select',
    default: '1h',
    options: [
      { value: '15m', label: '15 minutes' },
      { value: '1h', label: '1 heure' },
      { value: '6h', label: '6 heures' },
      { value: '24h', label: '24 heures' },
      { value: '7d', label: '7 jours' },
      { value: '30d', label: '30 jours' },
    ],
  },
  { key: 'live', label: 'Mise à jour en direct', type: 'boolean', default: true },
  { key: 'rangeSelector', label: 'Choix de la période par l\'opérateur', type: 'boolean', default: true },
  { key: 'yMin', label: 'Minimum de l\'axe', type: 'number', optional: true },
  { key: 'yMax', label: 'Maximum de l\'axe', type: 'number', optional: true },
  {
    key: 'theme',
    label: 'Thème',
    type: 'select',
    default: 'auto',
    options: [
      { value: 'auto', label: 'Automatique' },
      { value: 'light', label: 'Clair' },
      { value: 'dark', label: 'Sombre' },
    ],
  },
]
