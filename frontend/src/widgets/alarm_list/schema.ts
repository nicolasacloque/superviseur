import type { EditorField } from '../types'
import type { ThemePreference } from '../theme'
import { STATE_FILTERS, type StateFilter } from './alarms'

export const MAX_ROWS = 200

/** Configuration d'un widget `alarm_list` telle qu'enregistrée dans le JSON du synoptique. */
export interface AlarmListConfig {
  /** Préfixe du chemin des points (ex. `Site/Bat A`) ; vide = toutes les alarmes. */
  path?: string
  states: StateFilter
  maxRows: number
  allowAck: boolean
  theme: ThemePreference
}

export class AlarmListConfigError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'AlarmListConfigError'
  }
}

export function normalizeConfig(raw: unknown): AlarmListConfig {
  const config = (raw ?? {}) as Record<string, unknown>
  const states = config.states ?? 'open'
  if (!STATE_FILTERS.includes(states as StateFilter)) {
    throw new AlarmListConfigError(`alarm_list : filtre d'états inconnu « ${String(states)} »`)
  }
  const maxRows = config.maxRows ?? 50
  if (typeof maxRows !== 'number' || !Number.isInteger(maxRows) || maxRows < 1 || maxRows > MAX_ROWS) {
    throw new AlarmListConfigError(`alarm_list : maxRows doit être un entier entre 1 et ${MAX_ROWS}`)
  }
  const theme = config.theme ?? 'auto'
  if (theme !== 'auto' && theme !== 'light' && theme !== 'dark') {
    throw new AlarmListConfigError(`alarm_list : thème inconnu « ${String(theme)} »`)
  }
  if (config.path !== undefined && config.path !== null && typeof config.path !== 'string') {
    throw new AlarmListConfigError('alarm_list : path doit être un texte')
  }
  const path = typeof config.path === 'string' ? config.path.trim() : ''
  return {
    path: path || undefined,
    states: states as StateFilter,
    maxRows,
    allowAck: config.allowAck !== false,
    theme,
  }
}

/** Propriétés éditables : l'éditeur (Jalon 6) génère son panneau depuis cette description. */
export const editorSchema: EditorField[] = [
  { key: 'path', label: 'Chemin des points (préfixe)', type: 'text', optional: true, placeholder: 'Site/Bâtiment A' },
  {
    key: 'states',
    label: 'Alarmes affichées',
    type: 'select',
    default: 'open',
    options: [
      { value: 'open', label: 'Ouvertes' },
      { value: 'active', label: 'Actives' },
      { value: 'unacked', label: 'À acquitter' },
      { value: 'closed', label: 'Closes (historique)' },
      { value: 'all', label: 'Toutes' },
    ],
  },
  { key: 'maxRows', label: 'Nombre de lignes maximum', type: 'number', optional: true },
  { key: 'allowAck', label: "Bouton d'acquittement", type: 'boolean', default: true },
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
