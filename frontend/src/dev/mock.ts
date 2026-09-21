/** Données simulées pour la page de démonstration (`npm run dev`), sans API ni base. */

import { matches } from '../widgets/alarm_list/alarms'
import type { StateFilter } from '../widgets/alarm_list/alarms'
import type {
  Alarm,
  AlarmEvent,
  AlarmPage,
  AlarmQuery,
  AlarmsApi,
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

  private readonly alarmListeners = new Set<(event: AlarmEvent) => void>()

  subscribeAlarms(listener: (event: AlarmEvent) => void): () => void {
    this.alarmListeners.add(listener)
    return () => this.alarmListeners.delete(listener)
  }

  /** Diffuse une transition d'alarme aux widgets abonnés. */
  emitAlarm(event: AlarmEvent): void {
    for (const listener of [...this.alarmListeners]) listener(event)
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


const SAMPLE_ALARMS: Partial<Alarm>[] = [
  { rule_name: 'Soufflage trop chaud', severity: 'critical', path: 'Site/Bât A/CTA-1/Température soufflage', kind: 'high', value: 31.2, threshold: 28, unit: '°C', minutesAgo: 4 },
  { rule_name: 'Ventilateur en défaut', severity: 'critical', path: 'Site/Bât A/CTA-1/Défaut ventilateur', kind: 'state', value: 1, threshold: 1, unit: null, state: 'active_acked', acked_by: 'olivia', minutesAgo: 35 },
  { rule_name: 'Reprise trop froide', severity: 'warning', path: 'Site/Bât A/CTA-2/Température reprise', kind: 'low', value: 4.1, threshold: 5, unit: '°C', state: 'cleared_unacked', minutesAgo: 90 },
  { rule_name: 'Contrôleur injoignable', severity: 'warning', path: 'Site/Bât B/VAV-7/Température zone', kind: 'comm_lost', value: null, threshold: null, unit: null, minutesAgo: 12 },
  { rule_name: 'Sonde figée', severity: 'info', path: 'Site/Bât B/Extérieur/Température', kind: 'stale', value: 12.4, threshold: 300, unit: '°C', state: 'active_acked', acked_by: 'edgar', minutesAgo: 240 },
] as (Partial<Alarm> & { minutesAgo: number })[]

let sequence = 0

function makeAlarm(input: Partial<Alarm> & { minutesAgo?: number }): Alarm {
  sequence += 1
  const { minutesAgo = 0, ...rest } = input
  return {
    id: `mock-${sequence}`,
    rule_id: `rule-${sequence}`,
    rule_name: null,
    kind: 'high',
    severity: 'warning',
    state: 'active_unacked',
    point_id: `point-${sequence}`,
    point_name: 'Point',
    path: null,
    unit: null,
    threshold: null,
    value: null,
    raised_at: new Date(Date.now() - minutesAgo * 60_000).toISOString(),
    acked_at: null,
    acked_by: null,
    cleared_at: null,
    ...rest,
  }
}

/** Alarmes en mémoire : filtrage comme l'API, acquittement, et déclenchement à la demande. */
export class MockAlarms implements AlarmsApi {
  private items: Alarm[] = (SAMPLE_ALARMS as (Partial<Alarm> & { minutesAgo: number })[]).map(makeAlarm)

  constructor(private readonly live: MockLive) {}

  async alarms(query: AlarmQuery): Promise<AlarmPage> {
    await new Promise((resolve) => setTimeout(resolve, 150))
    const filter = { states: (query.state ?? 'open') as StateFilter, path: query.path }
    const items = this.items.filter((a) => matches(a, filter)).slice(0, query.limit ?? 100)
    return { items, total: items.length }
  }

  async acknowledge(alarmId: string): Promise<Alarm> {
    await new Promise((resolve) => setTimeout(resolve, 200))
    const found = this.items.find((a) => a.id === alarmId)
    if (!found) throw new Error('alarme introuvable')
    const acked: Alarm = {
      ...found,
      state: found.state === 'cleared_unacked' ? 'normal' : 'active_acked',
      acked_by: 'vous',
      acked_at: new Date().toISOString(),
    }
    this.items = this.items.map((a) => (a.id === alarmId ? acked : a))
    this.live.emitAlarm({ transition: 'acked', event: acked })
    return acked
  }

  /** Simule le déclenchement d'une nouvelle alarme, poussée en direct. */
  raise(): void {
    const alarm = makeAlarm({
      rule_name: 'Température haute (simulée)',
      severity: Math.random() > 0.5 ? 'critical' : 'warning',
      path: `Site/Bât A/CTA-${1 + (sequence % 3)}/Température`,
      value: 30 + Math.round(Math.random() * 40) / 10,
      threshold: 28,
      unit: '°C',
    })
    this.items = [alarm, ...this.items]
    this.live.emitAlarm({ transition: 'raised', event: alarm })
  }
}
