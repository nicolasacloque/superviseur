/** Contrats partagés entre l'API REST, le WebSocket et les widgets. */

export interface HistoryItem {
  ts: string
  value: number | null
  status?: string | null
  min?: number | null
  max?: number | null
  count?: number | null
}

export interface HistoryResult {
  point_id: string
  /** Tranche choisie par le serveur (`5m`, `1h`...), `null` pour des échantillons bruts. */
  bucket: string | null
  truncated: boolean
  items: HistoryItem[]
}

export interface HistoryQuery {
  from?: Date
  to?: Date
  /** `auto` : le serveur choisit une tranche ronde pour viser `maxPoints` points. */
  bucket?: string
  maxPoints?: number
}

export interface PointInfo {
  id: string
  name: string
  unit: string | null
  path: string | null
}

export interface HistoryApi {
  history(pointId: string, query: HistoryQuery, signal?: AbortSignal): Promise<HistoryResult>
  /** Nom et unité du point, pour la légende ; facultatif. */
  point?(pointId: string): Promise<PointInfo>
}

/** Valeur temps réel : `ts` en secondes depuis l'epoch. */
export interface LiveSample {
  point: string
  ts: number
  value: number | null
  status: string
}

export type LiveState = 'connecting' | 'open' | 'closed'

export interface LiveApi {
  readonly state: LiveState
  /** Reçoit les transitions d'alarme (`{"type":"alarm"}` du WebSocket). */
  subscribeAlarms(listener: (event: AlarmEvent) => void): () => void
  /** S'abonne aux points ; retourne la fonction de désabonnement. */
  subscribe(points: string[], listener: (sample: LiveSample) => void): () => void
  onState(listener: (state: LiveState) => void): () => void
}

export type AlarmState = 'active_unacked' | 'active_acked' | 'cleared_unacked' | 'normal'
export type AlarmSeverity = 'info' | 'warning' | 'critical'

export interface Alarm {
  id: string
  rule_id: string
  rule_name: string | null
  kind: string
  severity: AlarmSeverity
  state: AlarmState
  point_id: string
  point_name: string
  path: string | null
  unit: string | null
  threshold: number | null
  /** Valeur du point au déclenchement. */
  value: number | null
  raised_at: string
  acked_at: string | null
  acked_by: string | null
  cleared_at: string | null
}

/** Transition poussée par le serveur : `raised`, `cleared`, `acked` ou `normal`. */
export interface AlarmEvent {
  transition: string
  event: Alarm
}

export interface AlarmQuery {
  /** `open` (défaut), `active`, `unacked`, `closed`, `all` ou un état exact. */
  state?: string
  path?: string
  limit?: number
}

export interface AlarmPage {
  items: Alarm[]
  total: number
}

export interface AlarmsApi {
  alarms(query: AlarmQuery, signal?: AbortSignal): Promise<AlarmPage>
  /** Acquitte une alarme ; retourne l'alarme mise à jour. */
  acknowledge(alarmId: string): Promise<Alarm>
}
