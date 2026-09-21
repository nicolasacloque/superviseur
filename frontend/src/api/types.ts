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
  /** S'abonne aux points ; retourne la fonction de désabonnement. */
  subscribe(points: string[], listener: (sample: LiveSample) => void): () => void
  onState(listener: (state: LiveState) => void): () => void
}
