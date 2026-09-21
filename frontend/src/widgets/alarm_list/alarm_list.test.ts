import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import type { Alarm, AlarmEvent, AlarmPage, AlarmQuery, AlarmsApi, LiveApi, LiveSample, LiveState } from '../../api/types'
import { alarm } from './alarms.test'
import { AGE_REFRESH_MS, type AlarmListInstance, render } from './alarm_list'
import { AlarmListConfigError } from './schema'

class FakeApi implements AlarmsApi {
  queries: AlarmQuery[] = []
  items: Alarm[] = []
  failing: Error | null = null
  gate: Promise<void> | null = null
  acked: string[] = []
  ackError: Error | null = null
  async alarms(query: AlarmQuery): Promise<AlarmPage> {
    this.queries.push(query)
    if (this.gate) await this.gate
    if (this.failing) throw this.failing
    return { items: [...this.items], total: this.items.length }
  }
  async acknowledge(id: string): Promise<Alarm> {
    this.acked.push(id)
    if (this.ackError) throw this.ackError
    const found = this.items.find((a) => a.id === id)
    if (!found) throw new ApiError(409, 'introuvable')
    const updated: Alarm = {
      ...found,
      state: found.state === 'cleared_unacked' ? 'normal' : 'active_acked',
      acked_by: 'olivia',
      acked_at: '2026-09-21T12:00:00Z',
    }
    this.items = this.items.map((a) => (a.id === id ? updated : a))
    return updated
  }
}

class FakeLive implements LiveApi {
  state: LiveState = 'open'
  alarmListeners = new Set<(e: AlarmEvent) => void>()
  stateListeners = new Set<(s: LiveState) => void>()
  subscribe(_points: string[], _listener: (s: LiveSample) => void): () => void {
    return () => undefined
  }
  subscribeAlarms(listener: (e: AlarmEvent) => void): () => void {
    this.alarmListeners.add(listener)
    return () => this.alarmListeners.delete(listener)
  }
  onState(listener: (s: LiveState) => void): () => void {
    this.stateListeners.add(listener)
    return () => this.stateListeners.delete(listener)
  }
  emit(transition: string, event: Alarm): void {
    for (const listener of this.alarmListeners) listener({ transition, event })
  }
  setState(state: LiveState): void {
    this.state = state
    for (const listener of this.stateListeners) listener(state)
  }
}

let container: HTMLElement
let api: FakeApi
let live: FakeLive
let clock: number
let ticks: { fn: () => void; period: number; cancelled: boolean }[]
let instances: AlarmListInstance[]

function mount(config: unknown = {}, withLive = true): AlarmListInstance {
  const instance = render(container, config, {
    api,
    live: withLive ? live : undefined,
    now: () => clock,
    every: (fn, period) => {
      const tick = { fn, period, cancelled: false }
      ticks.push(tick)
      return () => {
        tick.cancelled = true
      }
    },
    prefersDark: () => false,
  })
  instances.push(instance)
  return instance
}

const rows = () => [...container.querySelectorAll<HTMLElement>('tbody tr')]
const rowText = (row: HTMLElement) => [...row.children].map((c) => c.textContent)
const ackButtons = () => [...container.querySelectorAll<HTMLButtonElement>('tbody button')]

beforeEach(() => {
  document.body.innerHTML = ''
  document.documentElement.removeAttribute('data-theme')
  container = document.createElement('div')
  document.body.append(container)
  api = new FakeApi()
  live = new FakeLive()
  clock = Date.parse('2026-09-21T12:00:00Z')
  ticks = []
  instances = []
})

afterEach(() => {
  for (const instance of instances) instance.destroy()
})

describe('chargement', () => {
  it('demande les alarmes du filtre configuré et les trie', async () => {
    const acked = alarm({ id: 'acked', state: 'active_acked', severity: 'critical', raised_at: '2026-09-21T11:00:00Z' })
    const waiting = alarm({ id: 'waiting', severity: 'warning', raised_at: '2026-09-21T09:00:00Z' })
    api.items = [acked, waiting]
    const widget = mount({ path: 'Site/Bat A', states: 'active', maxRows: 20 })
    expect(container.querySelector('.alarms__summary')?.textContent).toBe('Chargement des alarmes…')
    await widget.ready

    expect(api.queries).toEqual([{ state: 'active', path: 'Site/Bat A', limit: 20 }])
    expect(rows().map((r) => r.dataset.id)).toEqual(['waiting', 'acked']) // à acquitter d'abord
    expect(container.querySelector('.alarms__summary')?.textContent).toBe('2 alarmes dont 1 à acquitter')
    expect(container.querySelector('.alarms')?.getAttribute('aria-label')).toBe('Alarmes : Site/Bat A')
  })

  it('affiche la sévérité en forme et en texte, le point, la condition et l\'état', async () => {
    api.items = [alarm({ severity: 'critical', rule_name: 'Soufflage trop chaud', path: 'Site/CTA-1/Temp', state: 'cleared_unacked' })]
    await mount().ready
    const [row] = rows()
    expect(rowText(row!)).toEqual([
      'Critique',
      'Soufflage trop chaudSite/CTA-1/Temp',
      'Seuil haut : 30,5 °C > 28 °C',
      'Terminée, à acquitter',
      'il y a 2 h',
      'Acquitter',
    ])
    expect(row!.querySelector('.marker--critical')).not.toBeNull()
    expect(row!.classList.contains('is-waiting')).toBe(true)
  })

  it("dit clairement qu'il n'y a rien à signaler", async () => {
    await mount().ready
    expect(container.querySelector('.alarms__summary')?.textContent).toBe('Aucune alarme')
    expect(container.querySelector('.alarms__empty')?.textContent).toMatch(/Tout est normal/)
    expect(container.querySelector('table')).toBeNull()
  })

  it("signale l'indisponibilité de l'API sans planter", async () => {
    api.failing = new ApiError(500, 'HTTP 500')
    await mount().ready
    const message = container.querySelector<HTMLElement>('.alarms__message')!
    expect(message.hidden).toBe(false)
    expect(message.textContent).toBe('Alarmes indisponibles : HTTP 500')

    api.failing = null
    api.items = [alarm()]
    await instances[0]!.refresh()
    expect(message.hidden).toBe(true)
    expect(rows()).toHaveLength(1)
  })

  it('ignore la réponse d\'un chargement remplacé entre-temps', async () => {
    let release: () => void = () => {}
    api.gate = new Promise((resolve) => (release = resolve))
    api.items = [alarm({ id: 'old' })]
    const widget = mount()
    api.items = [alarm({ id: 'new' })]
    const second = widget.refresh()
    release()
    await Promise.all([widget.ready, second])
    expect(rows().map((r) => r.dataset.id)).toEqual(['new'])
  })

  it('refuse une configuration invalide', () => {
    expect(() => mount({ states: 'inconnu' })).toThrow(AlarmListConfigError)
    expect(container.querySelector('.alarms')).toBeNull()
  })
})

describe('acquittement', () => {
  it("acquitte l'alarme et met la ligne à jour", async () => {
    api.items = [alarm({ id: 'a1', rule_name: 'Haute' })]
    await mount().ready
    const [button] = ackButtons()
    expect(button?.getAttribute('aria-label')).toBe("Acquitter l'alarme Haute")
    button!.click()
    expect(ackButtons()[0]?.disabled).toBe(true) // pas de double clic pendant l'envoi
    await vi.waitFor(() => expect(rowText(rows()[0]!)[3]).toBe('Active, acquittéepar olivia'))
    expect(api.acked).toEqual(['a1'])
    expect(ackButtons()).toHaveLength(0) // plus rien à acquitter sur cette ligne
    expect(container.querySelector('.alarms__summary')?.textContent).toBe('1 alarme')
  })

  it('retire de la liste une alarme terminée qui vient d\'être acquittée (elle est close)', async () => {
    api.items = [alarm({ id: 'a1', state: 'cleared_unacked' })]
    await mount().ready
    ackButtons()[0]!.click()
    await vi.waitFor(() => expect(rows()).toHaveLength(0))
    expect(container.querySelector('.alarms__summary')?.textContent).toBe('Aucune alarme')
  })

  it('explique un refus de droits et resynchronise la liste', async () => {
    api.items = [alarm({ id: 'a1' })]
    await mount().ready
    api.ackError = new ApiError(403, 'droits insuffisants')
    ackButtons()[0]!.click()
    await vi.waitFor(() =>
      expect(container.querySelector('.alarms__message')?.textContent).toBe('Droits insuffisants pour acquitter cette alarme'),
    )
    expect(api.queries).toHaveLength(2) // rechargée après l'échec
    expect(ackButtons()[0]?.disabled).toBe(false) // on peut réessayer
  })

  it('affiche un autre échec tel que le serveur le décrit', async () => {
    api.items = [alarm({ id: 'a1' })]
    await mount().ready
    api.ackError = new ApiError(409, 'acquittement impossible dans l\'état active_acked')
    ackButtons()[0]!.click()
    await vi.waitFor(() =>
      expect(container.querySelector('.alarms__message')?.textContent).toBe(
        "Acquittement impossible : acquittement impossible dans l'état active_acked",
      ),
    )
  })

  it("n'affiche ni colonne ni bouton quand l'acquittement est désactivé", async () => {
    api.items = [alarm()]
    await mount({ allowAck: false }).ready
    expect([...container.querySelectorAll('th')].map((th) => th.textContent)).not.toContain('Action')
    expect(ackButtons()).toHaveLength(0)
  })
})

describe('temps réel', () => {
  it('ajoute les alarmes déclenchées et retire celles qui se ferment', async () => {
    const widget = mount({ states: 'open' })
    await widget.ready
    expect(rows()).toHaveLength(0)

    const raised = alarm({ id: 'live', severity: 'critical' })
    live.emit('raised', raised)
    expect(rows().map((r) => r.dataset.id)).toEqual(['live'])

    live.emit('acked', { ...raised, state: 'active_acked', acked_by: 'olivia' })
    expect(rowText(rows()[0]!)[3]).toBe('Active, acquittéepar olivia')

    live.emit('normal', { ...raised, state: 'normal' })
    expect(rows()).toHaveLength(0) // close : la liste des ouvertes ne la montre plus
  })

  it("applique le filtre de chemin aux événements reçus", async () => {
    await mount({ path: 'Site/Bat A' }).ready
    live.emit('raised', alarm({ id: 'other', path: 'Site/Bat B/x' }))
    live.emit('raised', alarm({ id: 'mine', path: 'Site/Bat A/x' }))
    expect(rows().map((r) => r.dataset.id)).toEqual(['mine'])
  })

  it("montre l'historique quand le filtre est « closes »", async () => {
    await mount({ states: 'closed' }).ready
    live.emit('normal', alarm({ id: 'done', state: 'normal' }))
    expect(rows().map((r) => r.dataset.id)).toEqual(['done'])
  })

  it('ignore les événements arrivés avant le premier chargement', async () => {
    let release: () => void = () => {}
    api.gate = new Promise((resolve) => (release = resolve))
    const widget = mount()
    live.emit('raised', alarm({ id: 'early' })) // sera de toute façon dans la réponse de l'API
    release()
    await widget.ready
    expect(rows()).toHaveLength(0)
  })

  it('signale la coupure du direct puis rattrape ce qui a été manqué à la reconnexion', async () => {
    await mount().ready
    const root = container.querySelector<HTMLElement>('.alarms')!
    live.setState('closed')
    expect(root.dataset.state).toBe('stale')
    expect(container.querySelector('.alarms__message')?.textContent).toMatch(/Connexion perdue/)

    api.items = [alarm({ id: 'missed' })] // apparue pendant la coupure
    live.setState('open')
    await vi.waitFor(() => expect(rows().map((r) => r.dataset.id)).toEqual(['missed']))
    expect(root.dataset.state).toBe('ok')
    expect(api.queries).toHaveLength(2)
  })

  it('fonctionne sans temps réel (rechargement manuel)', async () => {
    api.items = [alarm({ id: 'a' })]
    const widget = mount({}, false)
    await widget.ready
    expect(rows()).toHaveLength(1)
    api.items = []
    await widget.refresh()
    expect(rows()).toHaveLength(0)
  })

  it('rafraîchit les ancienneté toutes les 30 secondes', async () => {
    api.items = [alarm({ raised_at: '2026-09-21T11:59:30Z' })]
    await mount().ready
    expect(rowText(rows()[0]!)[4]).toBe("à l'instant")
    expect(ticks[0]?.period).toBe(AGE_REFRESH_MS)
    clock += 10 * 60 * 1000 // 10 min 30 s après le déclenchement
    ticks[0]!.fn()
    expect(rowText(rows()[0]!)[4]).toBe('il y a 11 min')
  })
})

describe('sécurité et fin de vie', () => {
  it('insère les textes du serveur sans les interpréter comme du HTML', async () => {
    const evil = '<img src=x onerror="window.pwned=1">'
    api.items = [alarm({ rule_name: evil, path: evil, point_name: evil, acked_by: evil, state: 'active_acked' })]
    await mount().ready
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('.name')?.textContent).toBe(evil)
    expect((window as unknown as { pwned?: number }).pwned).toBeUndefined()
  })

  it('se désabonne et retire son affichage', async () => {
    const widget = mount()
    await widget.ready
    expect(live.alarmListeners.size).toBe(1)
    widget.destroy()
    expect(live.alarmListeners.size).toBe(0)
    expect(live.stateListeners.size).toBe(0)
    expect(ticks.every((t) => t.cancelled)).toBe(true)
    expect(container.querySelector('.alarms')).toBeNull()
    live.emit('raised', alarm()) // sans effet
    expect(container.querySelector('tbody')).toBeNull()
  })

  it('annule un chargement en cours à la destruction', () => {
    const signals: AbortSignal[] = []
    api.alarms = vi.fn((_query: AlarmQuery, signal?: AbortSignal) => {
      if (signal) signals.push(signal)
      return new Promise<AlarmPage>(() => undefined)
    })
    const widget = mount()
    widget.destroy()
    expect(signals[0]?.aborted).toBe(true)
  })
})
