import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AlarmEvent, LiveApi, LiveSample, LiveState, PointInfo, SynopticRecord } from '../api/types'
import { newDoc, type SynopticDoc, type WidgetDef, type WidgetType } from '../synoptic/model'
import type { WidgetApi } from '../widgets/base'
import { MODULES } from '../widgets/registry'
import { fitScale, mountViewer, type ViewerInstance } from './viewer'

const P1 = '11111111-1111-4111-8111-111111111111'
const P2 = '22222222-2222-4222-8222-222222222222'

class FakeLive implements LiveApi {
  state: LiveState = 'open'
  subscriptions: string[][] = []
  unsubscribed = 0
  listeners = new Set<(s: LiveSample) => void>()
  stateListeners = new Set<(s: LiveState) => void>()
  subscribe(points: string[], listener: (s: LiveSample) => void): () => void {
    this.subscriptions.push(points)
    this.listeners.add(listener)
    return () => {
      this.unsubscribed++
      this.listeners.delete(listener)
    }
  }
  subscribeAlarms(_listener: (e: AlarmEvent) => void): () => void {
    return () => undefined
  }
  onState(listener: (s: LiveState) => void): () => void {
    this.stateListeners.add(listener)
    return () => this.stateListeners.delete(listener)
  }
  emit(point: string, value: number | null, status = 'ok'): void {
    for (const listener of this.listeners) listener({ point, ts: 0, value, status })
  }
  setState(state: LiveState): void {
    this.state = state
    for (const listener of this.stateListeners) listener(state)
  }
}

let container: HTMLElement
let live: FakeLive
let navigate: ReturnType<typeof vi.fn<(target: string) => void>>
let clock: number
let tasks: { fn: () => void; delay: number; cancelled: boolean; done: boolean }[]
let viewers: ViewerInstance[]
let writes: unknown[]

function widget(type: WidgetType, overrides: Partial<WidgetDef> = {}): WidgetDef {
  const defaults = MODULES[type].defaults() as WidgetDef
  const { bind, ...others } = overrides
  return { ...defaults, id: 'w1', type, ...others, bind: { ...defaults.bind, ...(bind ?? {}) } }
}

function record(widgets: WidgetDef[], patch: Partial<SynopticDoc> = {}): SynopticRecord {
  const doc = { ...newDoc('CTA 1'), widgets, ...patch }
  return { id: 'i', name: doc.name, slug: 'cta-1', version: 1, updated_at: '2026-09-21T10:00:00Z', doc }
}

function open(rec: SynopticRecord, role = 'operator'): ViewerInstance {
  const api: WidgetApi = {
    history: async () => ({ point_id: '', bucket: null, truncated: false, items: [] }),
    alarms: async () => ({ items: [], total: 0 }),
    acknowledge: async () => { throw new Error('non utilisé') },
    point: async (id): Promise<PointInfo> => ({ id, name: 'Temp', unit: '°C', path: null }),
    write: async (point, value, priority) => { writes.push({ point, value, priority }) },
  }
  const viewer = mountViewer(container, rec, {
    api, live, role, navigate,
    now: () => clock,
    schedule: (fn, delay) => {
      const task = { fn: () => { task.done = true; fn() }, delay, cancelled: false, done: false }
      tasks.push(task)
      return () => { task.cancelled = true }
    },
    confirm: async () => true,
  })  // fmt: skip
  viewers.push(viewer)
  return viewer
}

const flushBatch = () => tasks.filter((t) => !t.cancelled && !t.done).forEach((t) => t.fn())
const item = (id: string) => container.querySelector<HTMLElement>(`[data-widget-id="${id}"]`)!

beforeEach(() => {
  document.body.innerHTML = ''
  container = document.createElement('div')
  document.body.append(container)
  live = new FakeLive()
  navigate = vi.fn<(target: string) => void>()
  clock = 1000
  tasks = []
  viewers = []
  writes = []
})
afterEach(() => viewers.forEach((v) => v.destroy()))

describe('fitScale', () => {
  it('fait tenir le canevas sans le déformer', () => {
    const canvas = { width: 1920, height: 1080 }
    expect(fitScale({ width: 960, height: 540 }, canvas)).toBe(0.5)
    expect(fitScale({ width: 1920, height: 1080 }, canvas)).toBe(1)
    expect(fitScale({ width: 1000, height: 2000 }, canvas)).toBeCloseTo(1000 / 1920) // limité par la largeur
    expect(fitScale({ width: 4000, height: 500 }, canvas)).toBeCloseTo(500 / 1080) // limité par la hauteur
    expect(fitScale({ width: 3840, height: 2160 }, canvas)).toBe(2) // agrandi sur grand écran
    expect(fitScale({ width: 0, height: 0 }, canvas)).toBe(1) // pas encore mesuré
  })

  it('suit la taille de la zone d\'affichage', () => {
    let notify: () => void = () => undefined
    let width = 960
    vi.stubGlobal('ResizeObserver', class { constructor(cb: () => void) { notify = cb } observe() {} disconnect() {} })  // fmt: skip
    const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth')
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get() { return this.classList.contains('viewer') ? width : 0 } })  // fmt: skip
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get() { return this.classList.contains('viewer') ? 540 : 0 } })  // fmt: skip
    try {
      open(record([]))
      const root = container.querySelector<HTMLElement>('.viewer')!
      expect(root.dataset.scale).toBe('0.5000')
      expect(container.querySelector<HTMLElement>('.viewer__canvas')!.style.transform).toBe('scale(0.5)')
      expect(container.querySelector<HTMLElement>('.viewer__frame')!.style.width).toBe('960px')
      width = 480 // tablette en portrait : le canevas rétrécit proportionnellement
      notify()
      expect(root.dataset.scale).toBe('0.2500')
      expect(container.querySelector<HTMLElement>('.viewer__frame')!.style.height).toBe('270px')
    } finally {
      if (original) Object.defineProperty(HTMLElement.prototype, 'clientWidth', original)
      vi.unstubAllGlobals()
    }
  })
})

describe('mise en page', () => {
  it('place chaque widget à sa position et à sa taille dans le canevas', () => {
    open(record([widget('value', { id: 'a', x: 320, y: 140, w: 160, h: 48 }), widget('label', { id: 'b', x: 10, y: 20, w: 200, h: 32 })]))
    const canvas = container.querySelector<HTMLElement>('.viewer__canvas')!
    expect(canvas.style.width).toBe('1920px')
    expect(canvas.style.height).toBe('1080px')
    expect(canvas.style.background).toContain('rgb(15, 23, 42)') // #0f172a
    expect([item('a').style.left, item('a').style.top, item('a').style.width, item('a').style.height]).toEqual(['320px', '140px', '160px', '48px'])
    expect(item('b').dataset.type).toBe('label')
    expect(container.querySelector('.viewer')?.getAttribute('aria-label')).toBe('CTA 1')
  })

  it('adapte les textes par défaut au fond du canevas', () => {
    open(record([], { canvas: { width: 1920, height: 1080, background: '#ffffff', bg_image: null } }))
    expect(container.querySelector<HTMLElement>('.viewer__canvas')!.style.getPropertyValue('--syn-ink')).toBe('#0b0b0b')
    container.innerHTML = ''
    open(record([]))
    expect(container.querySelector<HTMLElement>('.viewer__canvas')!.style.getPropertyValue('--syn-ink')).toBe('#f8fafc')
  })

  it("n'applique un fond d'image que s'il s'agit d'une data URL d'image", () => {
    const png = 'data:image/png;base64,iVBORw0KGgo='
    open(record([], { canvas: { width: 800, height: 600, background: '#000', bg_image: png } }))
    expect(container.querySelector<HTMLElement>('.viewer__canvas')!.style.backgroundImage).toContain(png)
    container.innerHTML = ''
    open(record([], { canvas: { width: 800, height: 600, background: '#000', bg_image: 'https://evil/x.png' } }))
    expect(container.querySelector<HTMLElement>('.viewer__canvas')!.style.backgroundImage).not.toContain('evil')
  })

  it("n'empêche pas d'afficher le reste quand un widget est inconnu", () => {
    const viewer = open(record([widget('value', { id: 'ok' }), { ...widget('value', { id: 'bad' }), type: 'hologramme' as WidgetType }]))
    expect(viewer.rendered).toBe(2)
    expect(item('ok').querySelector('.w-value')).not.toBeNull()
    expect(item('bad').textContent).toContain('type de widget inconnu')
  })
})

describe('temps réel', () => {
  it('ouvre une seule souscription pour tous les points, sans doublon', () => {
    open(record([
      widget('value', { id: 'a', bind: { point: P1 } }),
      widget('gauge', { id: 'b', bind: { point: P1 } }),
      widget('indicator', { id: 'c', bind: { point: P2 } }),
      widget('label', { id: 'd' }),
    ]))  // fmt: skip
    expect(live.subscriptions).toHaveLength(1)
    expect([...live.subscriptions[0]!].sort()).toEqual([P1, P2])
  })

  it('ne souscrit pas quand aucun point n\'est lié', () => {
    open(record([widget('label')]))
    expect(live.subscriptions).toEqual([])
  })

  it('met à jour les widgets liés au point, et eux seuls', () => {
    open(record([
      widget('value', { id: 'a', bind: { point: P1, format: '0.0', unit: false } }),
      widget('value', { id: 'b', bind: { point: P2, format: '0.0', unit: false } }),
    ]))  // fmt: skip
    live.emit(P1, 21.46)
    flushBatch()
    expect(item('a').querySelector('.number span')?.textContent).toBe('21,5')
    expect(item('b').querySelector('.number span')?.textContent).toBe('—')
  })

  it('regroupe les rafales : une seule mise à jour de la dernière valeur', () => {
    open(record([widget('value', { id: 'a', bind: { point: P1, format: '0', unit: false } })]))
    for (let i = 1; i <= 100; i++) live.emit(P1, i)
    expect(tasks.filter((t) => !t.cancelled && !t.done)).toHaveLength(1)
    flushBatch()
    expect(item('a').querySelector('.number span')?.textContent).toBe('100')
  })

  it("grise les valeurs et prévient quand la connexion est perdue, puis rétablit", () => {
    open(record([widget('value', { bind: { point: P1 } })]))
    const root = container.querySelector<HTMLElement>('.viewer')!
    const banner = container.querySelector<HTMLElement>('.viewer__banner')!
    expect(banner.hidden).toBe(true)
    live.setState('closed')
    expect(root.dataset.state).toBe('stale')
    expect(banner.hidden).toBe(false)
    expect(banner.textContent).toMatch(/Connexion perdue/)
    live.setState('open')
    expect(root.dataset.state).toBe('ok')
    expect(banner.hidden).toBe(true)
  })
})

describe('navigation', () => {
  it('un widget lien ouvre le synoptique cible', () => {
    open(record([widget('link', { id: 'l', text: 'CTA 2', target: 'synoptic:cta-2' })]))
    item('l').querySelector('button')!.click()
    expect(navigate).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith('synoptic:cta-2')
  })

  it("la liste `links` rend n'importe quel widget cliquable, sans double navigation pour un lien", () => {
    open(record(
      [widget('shape', { id: 's' }), widget('link', { id: 'l', target: '' })],
      { links: [{ type: 'navigate', widget: 's', target: 'synoptic:vue-a' }, { type: 'navigate', widget: 'l', target: 'synoptic:vue-b' }] },
    ))  // fmt: skip
    item('s').click()
    expect(navigate).toHaveBeenLastCalledWith('synoptic:vue-a')
    expect(item('s').getAttribute('role')).toBe('link')
    item('s').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    expect(navigate).toHaveBeenCalledTimes(2)
    navigate.mockClear()
    item('l').querySelector('button')!.click() // la cible vient de `links` : un seul appel
    expect(navigate).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith('synoptic:vue-b')
  })
})

describe('droits', () => {
  const toggle = () => container.querySelector<HTMLButtonElement>('.w-switch button')!

  it('active les commandes pour un opérateur et les désactive pour un lecteur', () => {
    const rec = record([widget('switch', { bind: { point: P1 } })])
    open(rec, 'viewer')
    expect(toggle().disabled).toBe(true)
    expect(toggle().title).toMatch(/Droits insuffisants/)
    container.innerHTML = ''
    for (const role of ['operator', 'engineer', 'admin']) {
      container.innerHTML = ''
      open(rec, role)
      expect(toggle().disabled).toBe(false)
    }
  })

  it('écrit la commande avec le point et la priorité du widget', async () => {
    open(record([widget('switch', { bind: { point: P1 }, priority: 11 })]))
    live.emit(P1, 0)
    flushBatch()
    toggle().click()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(writes).toEqual([{ point: P1, value: 1, priority: 11 }])
  })
})

describe('fin de vie', () => {
  it('se désabonne, détruit les widgets et retire son affichage', () => {
    const viewer = open(record([widget('value', { bind: { point: P1 } })]))
    viewer.destroy()
    expect(live.unsubscribed).toBe(1)
    expect(live.stateListeners.size).toBe(0)
    expect(container.querySelector('.viewer')).toBeNull()
    live.emit(P1, 5) // sans effet
    expect(tasks.filter((t) => !t.cancelled && !t.done)).toHaveLength(0)
  })
})
