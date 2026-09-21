import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LiveClient, type SocketLike } from './live'
import type { LiveSample, LiveState } from './types'

class FakeSocket implements SocketLike {
  onopen: ((event: unknown) => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  sent: { action: string; points: string[] }[] = []
  closed = false
  send(data: string): void {
    this.sent.push(JSON.parse(data))
  }
  close(): void {
    this.closed = true
  }
  open(): void {
    this.onopen?.({})
  }
  push(message: unknown): void {
    this.onmessage?.({ data: typeof message === 'string' ? message : JSON.stringify(message) })
  }
  drop(code = 1006): void {
    this.onclose?.({ code })
  }
}

let sockets: FakeSocket[]
let timers: { fn: () => void; ms: number; cancelled: boolean }[]

function client(extra: Partial<ConstructorParameters<typeof LiveClient>[0]> = {}): LiveClient {
  return new LiveClient({
    url: 'ws://test/api/v1/ws',
    createSocket: () => {
      const socket = new FakeSocket()
      sockets.push(socket)
      return socket
    },
    setTimer: (fn, ms) => {
      const timer = { fn, ms, cancelled: false }
      timers.push(timer)
      return () => {
        timer.cancelled = true
      }
    },
    random: () => 1, // pas de gigue : les délais sont exacts
    ...extra,
  })
}

const value = (point: string, v: number, ts = '2026-09-21T10:00:00Z') => ({
  type: 'value',
  point,
  ts,
  value: v,
  status: 'ok',
})

beforeEach(() => {
  sockets = []
  timers = []
})

describe('LiveClient', () => {
  it('ne se connecte qu\'au premier abonnement et envoie les points à l\'ouverture', () => {
    const live = client()
    expect(sockets).toHaveLength(0)
    live.subscribe(['a', 'b'], () => {})
    expect(sockets).toHaveLength(1)
    expect(live.state).toBe('connecting')
    expect(sockets[0]?.sent).toEqual([]) // rien tant que la connexion n'est pas ouverte
    sockets[0]?.open()
    expect(live.state).toBe('open')
    expect(sockets[0]?.sent).toEqual([{ action: 'subscribe', points: ['a', 'b'] }])
  })

  it('partage un seul WebSocket et n\'envoie que les points nouveaux', () => {
    const live = client()
    live.subscribe(['a'], () => {})
    sockets[0]?.open()
    live.subscribe(['a', 'b'], () => {})
    expect(sockets).toHaveLength(1)
    expect(sockets[0]?.sent).toEqual([
      { action: 'subscribe', points: ['a'] },
      { action: 'subscribe', points: ['b'] },
    ])
  })

  it('distribue les valeurs aux abonnés du point, avec l\'instant en secondes', () => {
    const live = client()
    const received: LiveSample[] = []
    live.subscribe(['a'], (s) => received.push(s))
    sockets[0]?.open()
    sockets[0]?.push(value('a', 21.5))
    sockets[0]?.push(value('autre', 1)) // point non suivi
    sockets[0]?.push({ type: 'pong' })
    sockets[0]?.push({ type: 'error', message: 'x' })
    sockets[0]?.push('pas du json')
    expect(received).toEqual([
      { point: 'a', ts: Date.parse('2026-09-21T10:00:00Z') / 1000, value: 21.5, status: 'ok' },
    ])
  })

  it('compte les abonnements par point et se ferme quand plus personne n\'écoute', () => {
    const live = client()
    const first = vi.fn()
    const second = vi.fn()
    const offFirst = live.subscribe(['a'], first)
    const offSecond = live.subscribe(['a'], second)
    sockets[0]?.open()
    if (sockets[0]) sockets[0].sent = []

    offFirst()
    expect(sockets[0]?.sent).toEqual([]) // il reste un abonné
    sockets[0]?.push(value('a', 1))
    expect(second).toHaveBeenCalledTimes(1)
    expect(first).not.toHaveBeenCalled()

    offSecond()
    expect(sockets[0]?.sent).toEqual([{ action: 'unsubscribe', points: ['a'] }])
    expect(sockets[0]?.closed).toBe(true)
    expect(live.state).toBe('closed')
  })

  it('découpe les grandes souscriptions', () => {
    const live = client()
    live.subscribe(
      Array.from({ length: 450 }, (_, i) => `p${i}`),
      () => {},
    )
    sockets[0]?.open()
    expect(sockets[0]?.sent.map((m) => m.points.length)).toEqual([200, 200, 50])
  })

  it('se reconnecte avec un délai croissant puis reprend tous les abonnements', () => {
    const live = client({ minDelayMs: 1000, maxDelayMs: 4000 })
    const states: LiveState[] = []
    live.onState((s) => states.push(s))
    live.subscribe(['a', 'b'], () => {})
    sockets[0]?.open()

    sockets[0]?.drop()
    expect(live.state).toBe('closed')
    expect(timers.map((t) => t.ms)).toEqual([1000])
    timers[0]?.fn()
    sockets[1]?.drop() // la reconnexion échoue aussi
    sockets[1]?.drop() // (l'événement en double d'un socket déjà remplacé est ignoré)
    timers[1]?.fn()
    sockets[2]?.drop()
    timers[2]?.fn()
    sockets[3]?.drop()
    expect(timers.map((t) => t.ms)).toEqual([1000, 2000, 4000, 4000]) // plafonné

    timers[3]?.fn()
    sockets[4]?.open()
    expect(sockets[4]?.sent).toEqual([{ action: 'subscribe', points: ['a', 'b'] }])
    sockets[4]?.drop()
    expect(timers.at(-1)?.ms).toBe(1000) // le délai repart de zéro après une connexion réussie
    expect(states).toContain('connecting')
    expect(states.at(-1)).toBe('closed')
  })

  it('applique de la gigue au délai', () => {
    const live = client({ minDelayMs: 1000, random: () => 0 })
    live.subscribe(['a'], () => {})
    sockets[0]?.open()
    sockets[0]?.drop()
    expect(timers[0]?.ms).toBe(500)
  })

  it('renouvelle la session sur fermeture 4401 puis se reconnecte tout de suite', async () => {
    const refresh = vi.fn().mockResolvedValue(true)
    const live = client({ refresh })
    live.subscribe(['a'], () => {})
    sockets[0]?.open()
    sockets[0]?.drop(4401)
    await vi.waitFor(() => expect(sockets).toHaveLength(2))
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(timers).toHaveLength(0) // pas d'attente : la session vient d'être renouvelée
  })

  it('attend quand la session ne peut pas être renouvelée', async () => {
    const live = client({ refresh: () => Promise.resolve(false) })
    live.subscribe(['a'], () => {})
    sockets[0]?.open()
    sockets[0]?.drop(4401)
    await vi.waitFor(() => expect(timers).toHaveLength(1))
    expect(sockets).toHaveLength(1)
  })

  it('ne lance pas une seconde connexion pendant le renouvellement de session', async () => {
    let finish: (renewed: boolean) => void = () => {}
    const refresh = () => new Promise<boolean>((resolve) => (finish = resolve))
    const live = client({ refresh })
    live.subscribe(['a'], () => {})
    sockets[0]?.open()
    sockets[0]?.drop(4401)
    live.subscribe(['b'], () => {}) // un autre widget arrive pendant le renouvellement
    expect(sockets).toHaveLength(1)
    finish(true)
    await vi.waitFor(() => expect(sockets).toHaveLength(2))
    sockets[1]?.open()
    expect(sockets[1]?.sent).toEqual([{ action: 'subscribe', points: ['a', 'b'] }])
  })

  it('n\'écoute plus un socket fermé volontairement', () => {
    const live = client()
    const off = live.subscribe(['a'], () => {})
    sockets[0]?.open()
    off()
    sockets[0]?.drop() // l'événement de fermeture arrive après le désabonnement
    expect(timers).toHaveLength(0)
    expect(sockets).toHaveLength(1)
  })

  it('distribue les alarmes aux abonnés, sans abonnement de points', () => {
    const live = client()
    const seen: unknown[] = []
    const off = live.subscribeAlarms((event) => seen.push(event))
    expect(sockets).toHaveLength(1) // un widget d'alarmes suffit à ouvrir la connexion
    sockets[0]?.open()
    expect(sockets[0]?.sent).toEqual([]) // les alarmes n'exigent aucune déclaration
    const event = { transition: 'raised', event: { id: 'a1', state: 'active_unacked' } }
    sockets[0]?.push({ type: 'alarm', event })
    sockets[0]?.push({ type: 'alarm' }) // message sans contenu : ignoré
    expect(seen).toEqual([event])

    off()
    expect(sockets[0]?.closed).toBe(true) // plus personne n'écoute
  })

  it('garde la connexion tant qu\'un widget de points ou d\'alarmes reste', () => {
    const live = client()
    const offPoints = live.subscribe(['a'], () => {})
    const offAlarms = live.subscribeAlarms(() => {})
    sockets[0]?.open()
    offPoints()
    expect(sockets[0]?.closed).toBe(false) // les alarmes sont encore suivies
    offAlarms()
    expect(sockets[0]?.closed).toBe(true)
  })

  it('se reconnecte pour un simple abonné aux alarmes', () => {
    const live = client()
    live.subscribeAlarms(() => {})
    sockets[0]?.open()
    sockets[0]?.drop()
    timers[0]?.fn()
    expect(sockets).toHaveLength(2)
  })
})
