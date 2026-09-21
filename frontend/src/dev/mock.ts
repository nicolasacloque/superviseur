/** Données simulées pour la page de démonstration (`npm run dev`), sans API ni base. */

import type {
  HistoryApi,
  HistoryQuery,
  HistoryResult,
  LiveApi,
  LiveSample,
  LiveState,
  PointInfo,
} from '../api/types'

export const MOCK_POINTS: Record<string, { name: string; unit: string; base: number; amp: number; period: number }> = {
  soufflage: { name: 'Température soufflage', unit: '°C', base: 19, amp: 2.5, period: 3600 },
  reprise: { name: 'Température reprise', unit: '°C', base: 22, amp: 1.2, period: 5400 },
  exterieur: { name: 'Température extérieure', unit: '°C', base: 12, amp: 6, period: 86400 },
  puissance: { name: 'Puissance CTA', unit: 'kW', base: 14, amp: 5, period: 7200 },
  zone1: { name: 'Zone 1', unit: '°C', base: 19.5, amp: 1.2000000000000002, period: 3100 },
  zone2: { name: 'Zone 2', unit: '°C', base: 20.0, amp: 1.6, period: 3800 },
  zone3: { name: 'Zone 3', unit: '°C', base: 20.5, amp: 0.8, period: 4500 },
  zone4: { name: 'Zone 4', unit: '°C', base: 21.0, amp: 1.2000000000000002, period: 5200 },
  zone5: { name: 'Zone 5', unit: '°C', base: 21.5, amp: 1.6, period: 5900 },
  zone6: { name: 'Zone 6', unit: '°C', base: 22.0, amp: 0.8, period: 6600 },
  zone7: { name: 'Zone 7', unit: '°C', base: 22.5, amp: 1.2000000000000002, period: 7300 },
  zone8: { name: 'Zone 8', unit: '°C', base: 23.0, amp: 1.6, period: 8000 },
}

function valueAt(id: string, seconds: number): number {
  const p = MOCK_POINTS[id]
  if (!p) return 0
  const phase = id.length * 0.9
  const noise = Math.sin(seconds * 0.37 + phase) * p.amp * 0.01
  return p.base + p.amp * Math.sin((2 * Math.PI * seconds) / p.period + phase) + noise
}

export class MockApi implements HistoryApi {
  async history(pointId: string, query: HistoryQuery): Promise<HistoryResult> {
    await new Promise((resolve) => setTimeout(resolve, 150)) // latence réseau
    const to = (query.to ?? new Date()).getTime() / 1000
    const from = (query.from ?? new Date(to * 1000 - 86400 * 1000)).getTime() / 1000
    const count = Math.min(query.maxPoints ?? 300, 600)
    const step = (to - from) / count
    const items = Array.from({ length: count }, (_, i) => {
      const t = from + i * step
      // Un trou de communication au milieu de la courbe « reprise ».
      const lost = pointId === 'reprise' && i > count * 0.55 && i < count * 0.6
      return { ts: new Date(t * 1000).toISOString(), value: lost ? null : valueAt(pointId, t) }
    })
    return { point_id: pointId, bucket: '5m', truncated: false, items }
  }

  async point(pointId: string): Promise<PointInfo> {
    const p = MOCK_POINTS[pointId]
    if (!p) throw new Error('point inconnu')
    return { id: pointId, name: p.name, unit: p.unit, path: null }
  }
}

export class MockLive implements LiveApi {
  state: LiveState = 'open'
  private readonly listeners = new Map<string, Set<(s: LiveSample) => void>>()
  private readonly stateListeners = new Set<(s: LiveState) => void>()
  private timer = 0

  constructor(private readonly periodMs = 1000) {
    this.timer = window.setInterval(() => this.tick(), this.periodMs)
  }

  subscribe(points: string[], listener: (sample: LiveSample) => void): () => void {
    for (const point of points) {
      const group = this.listeners.get(point) ?? new Set()
      group.add(listener)
      this.listeners.set(point, group)
    }
    return () => points.forEach((point) => this.listeners.get(point)?.delete(listener))
  }

  onState(listener: (state: LiveState) => void): () => void {
    this.stateListeners.add(listener)
    return () => this.stateListeners.delete(listener)
  }

  /** Simule la coupure (ou le retour) du WebSocket. */
  setState(state: LiveState): void {
    this.state = state
    for (const listener of this.stateListeners) listener(state)
  }

  private tick(): void {
    if (this.state !== 'open') return
    const now = Date.now() / 1000
    for (const [point, group] of this.listeners) {
      const sample: LiveSample = { point, ts: now, value: valueAt(point, now), status: 'ok' }
      for (const listener of [...group]) listener(sample)
    }
  }
}
