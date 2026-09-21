/** Logique pure de la liste d'alarmes : filtres, tri, fusion des événements, libellés. */

import type { Alarm, AlarmSeverity, AlarmState } from '../../api/types'

export type StateFilter = 'open' | 'active' | 'unacked' | 'closed' | 'all'

export const STATE_FILTERS: StateFilter[] = ['open', 'active', 'unacked', 'closed', 'all']

export const STATE_GROUPS: Record<StateFilter, AlarmState[]> = {
  open: ['active_unacked', 'active_acked', 'cleared_unacked'],
  active: ['active_unacked', 'active_acked'],
  unacked: ['active_unacked', 'cleared_unacked'],
  closed: ['normal'],
  all: ['active_unacked', 'active_acked', 'cleared_unacked', 'normal'],
}

export const STATE_LABELS: Record<AlarmState, string> = {
  active_unacked: 'Active, à acquitter',
  active_acked: 'Active, acquittée',
  cleared_unacked: 'Terminée, à acquitter',
  normal: 'Close',
}

export const SEVERITY_LABELS: Record<AlarmSeverity, string> = {
  critical: 'Critique',
  warning: 'Avertissement',
  info: 'Information',
}

const SEVERITY_RANK: Record<AlarmSeverity, number> = { critical: 3, warning: 2, info: 1 }

export interface AlarmFilter {
  path?: string
  states: StateFilter
}

/** Une alarme attend-elle un acquittement de l'opérateur ? */
export function needsAck(alarm: Alarm): boolean {
  return alarm.state === 'active_unacked' || alarm.state === 'cleared_unacked'
}

export function matches(alarm: Alarm, filter: AlarmFilter): boolean {
  if (!STATE_GROUPS[filter.states].includes(alarm.state)) return false
  if (filter.path && !(alarm.path ?? '').startsWith(filter.path)) return false
  return true
}

/** À acquitter d'abord, puis la plus grave, puis la plus récente. */
export function compareAlarms(a: Alarm, b: Alarm): number {
  const ack = Number(needsAck(b)) - Number(needsAck(a))
  if (ack !== 0) return ack
  const severity = SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity]
  if (severity !== 0) return severity
  return Date.parse(b.raised_at) - Date.parse(a.raised_at) || a.id.localeCompare(b.id)
}

export function sortAlarms(alarms: Alarm[]): Alarm[] {
  return [...alarms].sort(compareAlarms)
}

/**
 * Applique une alarme reçue à la liste : elle est ajoutée ou mise à jour si elle correspond au
 * filtre, retirée sinon (ex. alarme close alors que la liste ne montre que les ouvertes).
 */
export function applyAlarm(list: Alarm[], alarm: Alarm, filter: AlarmFilter, maxRows: number): Alarm[] {
  const others = list.filter((a) => a.id !== alarm.id)
  if (!matches(alarm, filter)) return sortAlarms(others).slice(0, maxRows)
  return sortAlarms([...others, alarm]).slice(0, maxRows)
}

export function describeCondition(alarm: Alarm): string {
  const unit = alarm.unit ? ` ${alarm.unit}` : ''
  const number = (v: number | null) => (v === null ? '—' : `${new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 2 }).format(v)}${unit}`)
  switch (alarm.kind) {
    case 'high':
      return `Seuil haut : ${number(alarm.value)} > ${number(alarm.threshold)}`
    case 'low':
      return `Seuil bas : ${number(alarm.value)} < ${number(alarm.threshold)}`
    case 'state':
      return `État anormal (valeur ${number(alarm.value)})`
    case 'stale':
      return `Aucune nouvelle valeur depuis ${alarm.threshold ?? '?'} s`
    case 'comm_lost':
      return 'Communication perdue'
    case 'bacnet_event':
      return 'Événement signalé par le contrôleur'
    default:
      return alarm.kind
  }
}

/** « à l'instant », « il y a 5 min », « il y a 3 h », « il y a 2 j ». */
export function formatSince(iso: string, nowMs: number): string {
  const seconds = Math.max(0, Math.round((nowMs - Date.parse(iso)) / 1000))
  if (Number.isNaN(seconds)) return '—'
  if (seconds < 45) return "à l'instant"
  if (seconds < 3600) return `il y a ${Math.round(seconds / 60)} min`
  if (seconds < 86400) return `il y a ${Math.round(seconds / 3600)} h`
  return `il y a ${Math.round(seconds / 86400)} j`
}
