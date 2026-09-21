import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  HistoryApi,
  HistoryQuery,
  HistoryResult,
  LiveApi,
  LiveSample,
  LiveState,
  PointInfo,
} from '../../api/types'
import type { PlotAdapter, PlotFactory, PlotSpec } from './plot'
import { TrendConfigError } from './schema'
import { DARK, LIGHT } from '../theme'
import { MIN_DRAW_INTERVAL_MS, render, type TrendInstance, WINDOW_TICK_MS } from './trend'

const T0 = Date.parse('2026-09-21T12:00:00Z') // ms
const iso = (seconds: number) => new Date(seconds * 1000).toISOString()

class FakePlot implements PlotAdapter {
  data: PlotSpec['data']
  xRange: [number, number] = [0, 0]
  setDataCalls = 0
  destroyed = false
  cursor: ((index: number | null, left: number, top: number) => void) | null = null
  constructor(readonly spec: PlotSpec) {
    this.data = spec.data
  }
  setData(data: PlotSpec['data']): void {
    this.data = data
    this.setDataCalls++
  }
  setXRange(min: number, max: number): void {
    this.xRange = [min, max]
  }
  setSize(): void {}
  onCursor(listener: (index: number | null, left: number, top: number) => void): void {
    this.cursor = listener
  }
  destroy(): void {
    this.destroyed = true
  }
}

class FakeApi implements HistoryApi {
  calls: { point: string; query: HistoryQuery }[] = []
  items = new Map<string, HistoryResult['items']>()
  failing = new Set<string>()
  infos = new Map<string, PointInfo>()
  gate: Promise<void> | null = null
  async history(point: string, query: HistoryQuery): Promise<HistoryResult> {
    this.calls.push({ point, query })
    if (this.gate) await this.gate
    if (this.failing.has(point)) throw new Error('boom')
    return { point_id: point, bucket: '5m', truncated: false, items: this.items.get(point) ?? [] }
  }
  async point(point: string): Promise<PointInfo> {
    const info = this.infos.get(point)
    if (!info) throw new Error('inconnu')
    return info
  }
}

class FakeLive implements LiveApi {
  state: LiveState = 'open'
  listeners = new Set<(s: LiveSample) => void>()
  stateListeners = new Set<(s: LiveState) => void>()
  subscribed: string[][] = []
  unsubscribed = 0
  subscribe(points: string[], listener: (s: LiveSample) => void): () => void {
    this.subscribed.push(points)
    this.listeners.add(listener)
    return () => {
      this.unsubscribed++
      this.listeners.delete(listener)
    }
  }
  subscribeAlarms(): () => void {
    return () => undefined
  }
  onState(listener: (s: LiveState) => void): () => void {
    this.stateListeners.add(listener)
    return () => this.stateListeners.delete(listener)
  }
  emit(sample: LiveSample): void {
    for (const listener of this.listeners) listener(sample)
  }
  setState(state: LiveState): void {
    this.state = state
    for (const listener of this.stateListeners) listener(state)
  }
}

let container: HTMLElement
let clock: number
let scheduled: { fn: () => void; delay: number; cancelled: boolean; done: boolean }[]
let plots: FakePlot[]
let api: FakeApi
let live: FakeLive
let instances: TrendInstance[]

const createPlot: PlotFactory = (_host, spec) => {
  const plot = new FakePlot(spec)
  plots.push(plot)
  return plot
}

function mount(config: unknown, extra: { prefersDark?: boolean } = {}): TrendInstance {
  const instance = render(container, config, {
    api,
    live,
    createPlot,
    now: () => clock,
    schedule: (fn, delay) => {
      const task = {
        fn: () => {
          task.done = true
          fn()
        },
        delay,
        cancelled: false,
        done: false,
      }
      scheduled.push(task)
      return () => {
        task.cancelled = true
      }
    },
    prefersDark: () => extra.prefersDark ?? false,
  })
  instances.push(instance)
  return instance
}

/** Tâches de rendu en attente (hors tick de fenêtre glissante). */
const drawTasks = () =>
  scheduled.filter((t) => !t.cancelled && !t.done && t.delay !== WINDOW_TICK_MS)

function sample(point: string, seconds: number, value: number | null, status = 'ok'): LiveSample {
  return { point, ts: seconds, value, status }
}

beforeEach(() => {
  document.body.innerHTML = ''
  document.documentElement.removeAttribute('data-theme')
  container = document.createElement('div')
  document.body.append(container)
  clock = T0
  scheduled = []
  plots = []
  api = new FakeApi()
  live = new FakeLive()
  instances = []
})

afterEach(() => {
  for (const instance of instances) instance.destroy()
})

const config = (points: object[], extra: object = {}) => ({ bind: { points }, ...extra })

describe("chargement de l'historique", () => {
  it('demande une tranche automatique sur la période, pour chaque point', async () => {
    const trend = mount(config([{ point: 'a' }, { point: 'b' }], { range: '6h' }))
    await trend.ready
    expect(api.calls.map((c) => c.point)).toEqual(['a', 'b'])
    const query = api.calls[0]?.query
    expect(query?.bucket).toBe('auto')
    expect(query?.to?.getTime()).toBe(T0)
    expect(query?.from?.getTime()).toBe(T0 - 6 * 3600 * 1000)
    expect(query?.maxPoints).toBe(320) // 640 px par défaut : un point tous les 2 px
  })

  it("trace les séries alignées, dans l'ordre et avec les couleurs fixes de la palette", async () => {
    const t = T0 / 1000
    api.items.set('a', [
      { ts: iso(t - 600), value: 20 },
      { ts: iso(t - 300), value: 21 },
    ])
    api.items.set('b', [{ ts: iso(t - 300), value: 50 }])
    const trend = mount(config([{ point: 'a', label: 'Soufflage' }, { point: 'b' }]))
    await trend.ready

    const plot = plots[0]!
    expect(plot.data).toEqual([[t - 600, t - 300], [20, 21], [null, 50]])
    expect(plot.spec.series.map((s) => s.color)).toEqual([LIGHT.series[0], LIGHT.series[1]])
    expect(plot.spec.series[0]?.label).toBe('Soufflage')
    expect(plot.xRange).toEqual([t - 3600, t])
  })

  it('suit le thème sombre (système ou thème stampé sur la page)', async () => {
    const system = mount(config([{ point: 'a' }]), { prefersDark: true })
    await system.ready
    expect(plots[0]?.spec.series[0]?.color).toBe(DARK.series[0])

    document.documentElement.dataset.theme = 'dark'
    const stamped = mount(config([{ point: 'a' }], { theme: 'auto' }))
    await stamped.ready
    expect(plots[1]?.spec.theme.mode).toBe('dark')
    const forced = mount(config([{ point: 'a' }], { theme: 'light' }), { prefersDark: true })
    await forced.ready
    expect(plots[2]?.spec.theme.mode).toBe('light')
  })

  it("utilise le nom et l'unité du point dans la légende", async () => {
    api.infos.set('a', { id: 'a', name: 'Temp soufflage', unit: '°C', path: null })
    api.items.set('a', [{ ts: iso(T0 / 1000 - 60), value: 21.4 }])
    const trend = mount(config([{ point: 'a' }, { point: 'b', label: 'Libellé imposé' }]))
    await trend.ready
    const names = [...container.querySelectorAll('.trend__legend .name')].map((n) => n.textContent)
    expect(names).toEqual(['Temp soufflage', 'Libellé imposé'])
    expect(container.querySelector('.trend__legend .last')?.textContent).toBe('21,4 °C')
  })

  it("affiche ce qui est disponible quand l'historique de certains points échoue", async () => {
    api.items.set('a', [{ ts: iso(T0 / 1000 - 60), value: 1 }])
    api.failing.add('b')
    const trend = mount(config([{ point: 'a' }, { point: 'b' }]))
    await trend.ready
    expect(plots[0]?.data[1]).toEqual([1])
    const message = container.querySelector<HTMLElement>('.trend__message')!
    expect(message.hidden).toBe(false)
    expect(message.textContent).toBe('Historique indisponible pour 1 courbe')
  })

  it('refuse une configuration invalide', () => {
    expect(() => mount(config([]))).toThrow(TrendConfigError)
    expect(() => mount(config(Array.from({ length: 9 }, (_, i) => ({ point: `p${i}` }))))).toThrow(
      /8 points/,
    )
    expect(container.querySelector('.trend')).toBeNull() // rien n'est ajouté à la page
  })
})

describe('temps réel', () => {
  it("s'abonne aux points et ajoute les valeurs à la courbe", async () => {
    const t = T0 / 1000
    const trend = mount(config([{ point: 'a' }, { point: 'b' }]))
    await trend.ready
    expect(live.subscribed).toEqual([['a', 'b']])

    clock += 10_000
    live.emit(sample('a', t + 10, 22))
    drawTasks()[0]?.fn()
    expect(plots[0]?.data[0]).toContain(t + 10)
    expect(plots[0]?.data[1]).toContain(22)
    expect(plots[0]?.xRange[1]).toBe(t + 10) // la fenêtre suit l'heure courante
  })

  it('regroupe les mises à jour : un seul rendu planifié, jamais plus de 10 par seconde', async () => {
    const t = T0 / 1000
    const trend = mount(config([{ point: 'a' }]))
    await trend.ready
    const before = plots[0]?.setDataCalls ?? 0

    for (let i = 1; i <= 50; i++) live.emit(sample('a', t + i * 0.01, i))
    expect(drawTasks()).toHaveLength(1)
    drawTasks()[0]?.fn()
    expect(plots[0]?.setDataCalls).toBe(before + 1) // 50 valeurs, un seul tracé

    // Une valeur juste après : le rendu est retardé pour laisser 100 ms entre deux tracés.
    clock += 30
    live.emit(sample('a', t + 1, 99))
    const next = drawTasks().at(-1)
    expect(next?.delay).toBe(MIN_DRAW_INTERVAL_MS - 30)
    live.emit(sample('a', t + 2, 100)) // un rendu est déjà planifié : pas de second
    expect(drawTasks()).toHaveLength(1)
  })

  it("applique les valeurs arrivées pendant le chargement de l'historique", async () => {
    const t = T0 / 1000
    let release: () => void = () => {}
    api.gate = new Promise((resolve) => (release = resolve))
    const trend = mount(config([{ point: 'a' }]))
    live.emit(sample('a', t + 5, 7)) // l'historique n'est pas encore là
    expect(plots).toHaveLength(0)
    release()
    await trend.ready
    expect(plots[0]?.data[0]).toContain(t + 5)
    expect(plots[0]?.data[1]).toContain(7)
  })

  it("ignore un point qui n'est pas dans la courbe", async () => {
    const trend = mount(config([{ point: 'a' }]))
    await trend.ready
    trend.update(sample('inconnu', T0 / 1000 + 1, 1))
    expect(drawTasks()).toHaveLength(0)
  })

  it('transforme une perte de communication en trou, pas en zéro', async () => {
    const t = T0 / 1000
    const trend = mount(config([{ point: 'a' }]))
    await trend.ready
    // Espacés de plus de 3,6 s (3600 s / 1000 points) : sinon les valeurs seraient regroupées.
    live.emit(sample('a', t + 10, 20))
    live.emit(sample('a', t + 20, null, 'comm_lost'))
    live.emit(sample('a', t + 30, 21))
    drawTasks()[0]?.fn()
    expect(plots[0]?.data[1]).toEqual([20, null, 21])
  })

  it('fait glisser la fenêtre et retire les points trop anciens', async () => {
    const t = T0 / 1000
    api.items.set('a', [
      { ts: iso(t - 3000), value: 1 },
      { ts: iso(t - 100), value: 2 },
    ])
    const trend = mount(config([{ point: 'a' }], { range: '1h' }))
    await trend.ready
    expect(plots[0]?.data[1]).toEqual([1, 2])

    clock += 1000 * 1000 // 1000 s plus tard : le premier point sort de l'heure affichée
    scheduled.find((s) => s.delay === WINDOW_TICK_MS && !s.cancelled)?.fn()
    drawTasks()[0]?.fn()
    expect(plots[0]?.data[1]).toEqual([2])
    expect(plots[0]?.xRange).toEqual([t + 1000 - 3600, t + 1000])
  })

  it("ne s'abonne pas quand le direct est désactivé", async () => {
    const trend = mount(config([{ point: 'a' }], { live: false }))
    await trend.ready
    expect(live.subscribed).toEqual([])
    expect(scheduled.filter((s) => s.delay === WINDOW_TICK_MS)).toHaveLength(0)
  })
})

describe('connexion perdue', () => {
  it("signale l'arrêt du direct, estompe la courbe puis revient à la normale", async () => {
    const trend = mount(config([{ point: 'a' }]))
    await trend.ready
    const root = container.querySelector<HTMLElement>('.trend')!
    const message = container.querySelector<HTMLElement>('.trend__message')!
    expect(root.dataset.state).toBe('ok')

    live.setState('closed')
    expect(root.dataset.state).toBe('stale')
    expect(message.hidden).toBe(false)
    expect(message.textContent).toMatch(/Connexion perdue/)
    expect(message.getAttribute('role')).toBe('status')

    live.setState('open')
    expect(root.dataset.state).toBe('ok')
    expect(message.hidden).toBe(true)
  })
})

describe('période', () => {
  it("recharge l'historique en gardant le cadre, et le bouton reflète la période", async () => {
    const trend = mount(config([{ point: 'a' }], { range: '1h' }))
    await trend.ready
    const buttons = [...container.querySelectorAll<HTMLButtonElement>('.trend__bar button')]
    const pressed = () =>
      buttons.filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => b.textContent)
    expect(pressed()).toEqual(['1 h'])

    api.calls = []
    const root = container.querySelector('.trend')!
    let release: () => void = () => {}
    api.gate = new Promise((resolve) => (release = resolve))
    buttons.find((b) => b.textContent === '24 h')?.click()
    expect(root.classList.contains('is-refetching')).toBe(true) // ancien tracé conservé, estompé
    expect(pressed()).toEqual(['24 h'])
    release()
    await vi.waitFor(() => expect(root.classList.contains('is-refetching')).toBe(false))
    expect(api.calls[0]?.query.from?.getTime()).toBe(T0 - 24 * 3600 * 1000)
    expect(plots[0]?.xRange[0]).toBe(T0 / 1000 - 86400)
  })

  it("ignore la réponse d'une période remplacée entre-temps", async () => {
    const t = T0 / 1000
    const trend = mount(config([{ point: 'a' }], { range: '1h' }))
    await trend.ready
    const releases: (() => void)[] = []
    const results = [
      [{ ts: iso(t - 10), value: 111 }], // réponse lente, à ignorer
      [{ ts: iso(t - 20), value: 222 }],
    ]
    api.history = vi.fn(
      (point: string) =>
        new Promise<HistoryResult>((resolve) => {
          const items = results.shift() ?? []
          releases.push(() => resolve({ point_id: point, bucket: null, truncated: false, items }))
        }),
    )
    const slow = trend.setRange('6h')
    const fast = trend.setRange('24h')
    releases[1]?.()
    await fast
    releases[0]?.()
    await slow
    expect(plots[0]?.data[1]).toEqual([222])
  })

  it('retire le sélecteur quand la configuration le demande', async () => {
    const trend = mount(config([{ point: 'a' }], { rangeSelector: false }))
    await trend.ready
    expect(container.querySelectorAll('.trend__bar button')).toHaveLength(1) // seulement « Tableau »
  })
})

describe('lecture accessible', () => {
  it("propose une vue tableau des dernières valeurs, la plus récente d'abord", async () => {
    const t = T0 / 1000
    api.items.set('a', [
      { ts: iso(t - 120), value: 20 },
      { ts: iso(t - 60), value: 21 },
    ])
    const trend = mount(config([{ point: 'a', label: 'Soufflage' }]))
    await trend.ready
    const toggle = [...container.querySelectorAll<HTMLButtonElement>('.trend__bar button')].find(
      (b) => b.textContent === 'Tableau',
    )!
    const table = container.querySelector<HTMLElement>('.trend__table')!
    expect(table.hidden).toBe(true)
    toggle.click()
    expect(table.hidden).toBe(false)
    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    const rows = [...table.querySelectorAll('tr')].map((tr) => [...tr.children].map((c) => c.textContent))
    expect(rows).toEqual([
      ['Heure', 'Soufflage'],
      [expect.any(String), '21'],
      [expect.any(String), '20'],
    ])
  })

  it('insère les libellés en texte, jamais en HTML', async () => {
    const label = '<img src=x onerror="window.pwned=1">'
    const trend = mount(config([{ point: 'a', label }]))
    await trend.ready
    expect(container.querySelector('.trend img')).toBeNull()
    expect(container.querySelector('.trend__legend .name')?.textContent).toBe(label)
    plots[0]?.cursor?.(0, 40, 20)
    expect(container.querySelector('.trend__tooltip img')).toBeNull()
    expect((window as unknown as { pwned?: number }).pwned).toBeUndefined()
  })

  it("affiche dans l'infobulle la valeur de chaque série à l'instant survolé", async () => {
    const t = T0 / 1000
    api.items.set('a', [{ ts: iso(t - 60), value: 20.5 }])
    api.items.set('b', [{ ts: iso(t - 60), value: 60 }])
    api.infos.set('a', { id: 'a', name: 'Température', unit: '°C', path: null })
    const trend = mount(config([{ point: 'a' }, { point: 'b', label: 'Humidité' }]))
    await trend.ready
    const tooltip = container.querySelector<HTMLElement>('.trend__tooltip')!

    plots[0]?.cursor?.(0, 100, 30)
    expect(tooltip.hidden).toBe(false)
    const rows = [...tooltip.querySelectorAll('.row')].map((r) =>
      [...r.querySelectorAll('.value, .label')].map((n) => n.textContent),
    )
    expect(rows).toEqual([
      ['20,5 °C', 'Température'],
      ['60', 'Humidité'],
    ])

    plots[0]?.cursor?.(null, -1, -1)
    expect(tooltip.hidden).toBe(true)
  })

  it("décrit la courbe aux lecteurs d'écran", async () => {
    const trend = mount(config([{ point: 'a', label: 'Soufflage' }, { point: 'b', label: 'Reprise' }]))
    await trend.ready
    const root = container.querySelector('.trend')!
    expect(root.getAttribute('role')).toBe('group')
    expect(root.getAttribute('aria-label')).toBe('Courbe de tendance : Soufflage, Reprise')
  })
})

describe('fin de vie', () => {
  it('se désabonne, annule les rendus et retire le widget', async () => {
    const trend = mount(config([{ point: 'a' }]))
    await trend.ready
    live.emit(sample('a', T0 / 1000 + 1, 1))
    expect(drawTasks()).toHaveLength(1)

    trend.destroy()
    expect(live.unsubscribed).toBe(1)
    expect(live.stateListeners.size).toBe(0)
    expect(plots[0]?.destroyed).toBe(true)
    expect(scheduled.every((task) => task.cancelled)).toBe(true)
    expect(container.querySelector('.trend')).toBeNull()

    trend.update(sample('a', T0 / 1000 + 2, 2)) // sans effet
    expect(scheduled.filter((task) => !task.cancelled)).toHaveLength(0)
  })

  it('annule un chargement encore en cours', () => {
    const signals: AbortSignal[] = []
    api.history = vi.fn((_p: string, _q: HistoryQuery, signal?: AbortSignal) => {
      if (signal) signals.push(signal)
      return new Promise<HistoryResult>(() => {})
    })
    const trend = mount(config([{ point: 'a' }]))
    trend.destroy()
    expect(signals[0]?.aborted).toBe(true)
  })
})
