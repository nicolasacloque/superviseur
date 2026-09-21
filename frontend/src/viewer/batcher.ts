import type { LiveSample } from '../api/types'

export type Schedule = (fn: () => void, delayMs: number) => () => void

/** Au plus 10 mises à jour par seconde et par widget (section 10.5). */
export const MIN_RENDER_INTERVAL_MS = 100

/**
 * Regroupe les valeurs reçues du WebSocket : un widget ne reçoit que la dernière valeur de chaque
 * point, au plus une fois tous les `minIntervalMs`, dans un rendu planifié (requestAnimationFrame).
 */
export class UpdateBatcher {
  private readonly pending = new Map<string, Map<string, LiveSample>>()
  private readonly lastRender = new Map<string, number>()
  private cancel: (() => void) | null = null
  private destroyed = false

  constructor(
    private readonly deliver: (widgetId: string, sample: LiveSample) => void,
    private readonly options: { minIntervalMs?: number; now?: () => number; schedule?: Schedule } = {},
  ) {}

  private get minInterval(): number {
    return this.options.minIntervalMs ?? MIN_RENDER_INTERVAL_MS
  }

  private now(): number {
    return (this.options.now ?? (() => performance.now()))()
  }

  push(widgetId: string, sample: LiveSample): void {
    if (this.destroyed) return
    let samples = this.pending.get(widgetId)
    if (!samples) this.pending.set(widgetId, (samples = new Map()))
    samples.set(sample.point, sample) // seule la plus récente compte
    this.plan()
  }

  /** Prochain instant où un widget en attente peut être rendu. */
  private plan(): void {
    if (this.cancel || this.pending.size === 0) return
    const now = this.now()
    let wait = Infinity
    for (const widgetId of this.pending.keys()) {
      const due = (this.lastRender.get(widgetId) ?? -Infinity) + this.minInterval
      wait = Math.min(wait, Math.max(0, due - now))
    }
    this.cancel = (this.options.schedule ?? defaultSchedule)(() => {
      this.cancel = null
      this.flush()
    }, wait)
  }

  private flush(): void {
    if (this.destroyed) return
    const now = this.now()
    for (const [widgetId, samples] of [...this.pending]) {
      if (now - (this.lastRender.get(widgetId) ?? -Infinity) < this.minInterval) continue
      this.pending.delete(widgetId)
      this.lastRender.set(widgetId, now)
      for (const sample of samples.values()) this.deliver(widgetId, sample)
    }
    this.plan() // les widgets encore trop récents seront rendus au prochain créneau
  }

  destroy(): void {
    this.destroyed = true
    this.cancel?.()
    this.cancel = null
    this.pending.clear()
  }
}

function defaultSchedule(fn: () => void, delayMs: number): () => void {
  let frame = 0
  const timer = setTimeout(() => {
    if (typeof requestAnimationFrame === 'function') frame = requestAnimationFrame(fn)
    else fn()
  }, delayMs)
  return () => {
    clearTimeout(timer)
    if (frame && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame)
  }
}
