import type { LiveApi, LiveSample, LiveState } from './types'

/** Le sous-ensemble de WebSocket utilisé : permet de le remplacer dans les tests. */
export interface SocketLike {
  onopen: ((event: unknown) => void) | null
  onmessage: ((event: { data: unknown }) => void) | null
  onclose: ((event: { code: number }) => void) | null
  onerror: ((event: unknown) => void) | null
  send(data: string): void
  close(): void
}

export interface LiveOptions {
  /** URL du WebSocket, ex. `wss://hote/api/v1/ws`. */
  url: string
  /** Renouvelle la session (`POST /auth/refresh`) ; `true` si elle a été renouvelée. */
  refresh?: () => Promise<boolean>
  createSocket?: (url: string) => SocketLike
  setTimer?: (callback: () => void, delayMs: number) => () => void
  random?: () => number
  minDelayMs?: number
  maxDelayMs?: number
}

const CLOSE_UNAUTHORIZED = 4401
const SUBSCRIBE_CHUNK = 200

/**
 * Connexion temps réel partagée : un seul WebSocket pour tous les widgets de la page,
 * abonnements comptés par point, reconnexion avec délai croissant, renouvellement de la
 * session quand le serveur ferme avec 4401.
 */
export class LiveClient implements LiveApi {
  private socket: SocketLike | null = null
  private current: LiveState = 'closed'
  private readonly listeners = new Map<string, Set<(sample: LiveSample) => void>>()
  private readonly stateListeners = new Set<(state: LiveState) => void>()
  private attempts = 0
  private cancelRetry: (() => void) | null = null
  private reconnecting = false

  constructor(private readonly options: LiveOptions) {}

  get state(): LiveState {
    return this.current
  }

  onState(listener: (state: LiveState) => void): () => void {
    this.stateListeners.add(listener)
    return () => this.stateListeners.delete(listener)
  }

  subscribe(points: string[], listener: (sample: LiveSample) => void): () => void {
    const added: string[] = []
    for (const point of points) {
      let group = this.listeners.get(point)
      if (!group) {
        group = new Set()
        this.listeners.set(point, group)
        added.push(point)
      }
      group.add(listener)
    }
    if (this.socket === null && this.cancelRetry === null && !this.reconnecting) this.connect()
    else if (this.current === 'open') this.send('subscribe', added)

    return () => {
      const removed: string[] = []
      for (const point of points) {
        const group = this.listeners.get(point)
        if (!group?.delete(listener)) continue
        if (group.size === 0) {
          this.listeners.delete(point)
          removed.push(point)
        }
      }
      if (this.current === 'open') this.send('unsubscribe', removed)
      if (this.listeners.size === 0) this.disconnect()
    }
  }

  private setState(state: LiveState): void {
    if (state === this.current) return
    this.current = state
    for (const listener of this.stateListeners) listener(state)
  }

  private send(action: 'subscribe' | 'unsubscribe', points: string[]): void {
    for (let start = 0; start < points.length; start += SUBSCRIBE_CHUNK) {
      const chunk = points.slice(start, start + SUBSCRIBE_CHUNK)
      this.socket?.send(JSON.stringify({ action, points: chunk }))
    }
  }

  private connect(): void {
    this.cancelRetry = null
    this.reconnecting = false
    this.setState('connecting')
    const socket = (this.options.createSocket ?? defaultSocket)(this.options.url)
    this.socket = socket
    socket.onopen = () => {
      if (this.socket !== socket) return
      this.attempts = 0
      this.setState('open')
      this.send('subscribe', [...this.listeners.keys()]) // reprise des abonnements après coupure
    }
    socket.onmessage = (event) => {
      if (this.socket === socket) this.dispatch(event.data)
    }
    socket.onclose = (event) => {
      if (this.socket !== socket) return // socket déjà remplacé ou fermé volontairement
      this.socket = null
      this.setState('closed')
      if (this.listeners.size > 0) void this.reconnect(event.code)
    }
  }

  private async reconnect(code: number): Promise<void> {
    this.reconnecting = true
    const setTimer = this.options.setTimer ?? defaultTimer
    if (code === CLOSE_UNAUTHORIZED && this.options.refresh) {
      let renewed = false
      try {
        renewed = await this.options.refresh()
      } catch {
        renewed = false
      }
      if (renewed && this.listeners.size > 0) return this.connect()
    }
    const min = this.options.minDelayMs ?? 1000
    const max = this.options.maxDelayMs ?? 30000
    const random = this.options.random ?? Math.random
    const delay = Math.min(max, min * 2 ** this.attempts) * (0.5 + random() * 0.5)
    this.attempts += 1
    this.reconnecting = false
    this.cancelRetry = setTimer(() => {
      if (this.listeners.size > 0) this.connect()
      else this.cancelRetry = null
    }, delay)
  }

  private disconnect(): void {
    this.cancelRetry?.()
    this.cancelRetry = null
    const socket = this.socket
    this.socket = null
    socket?.close()
    this.setState('closed')
  }

  private dispatch(data: unknown): void {
    if (typeof data !== 'string') return
    let message: { type?: string; point?: string; ts?: string; value?: number | null; status?: string }
    try {
      message = JSON.parse(data)
    } catch {
      return
    }
    if (message.type !== 'value' || !message.point || !message.ts) return
    const group = this.listeners.get(message.point)
    if (!group) return
    const sample: LiveSample = {
      point: message.point,
      ts: Date.parse(message.ts) / 1000,
      value: message.value ?? null,
      status: message.status ?? 'ok',
    }
    for (const listener of [...group]) listener(sample)
  }
}

function defaultSocket(url: string): SocketLike {
  const absolute = url.startsWith('/')
    ? `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${url}`
    : url
  return new WebSocket(absolute) as unknown as SocketLike
}

function defaultTimer(callback: () => void, delayMs: number): () => void {
  const handle = setTimeout(callback, delayMs)
  return () => clearTimeout(handle)
}
