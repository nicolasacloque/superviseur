import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LiveSample, PointInfo } from '../api/types'
import type { WidgetDef, WidgetType } from '../synoptic/model'
import { formatNumber, type WidgetContext } from './base'
import { MODULES } from './registry'
import type { WidgetInstance } from './types'

let container: HTMLElement
let instances: WidgetInstance[]
let writes: { point: string; value: number | null; priority: number }[]
let confirmations: string[]

const POINT = '11111111-1111-4111-8111-111111111111'

function context(overrides: Partial<WidgetContext> = {}): WidgetContext {
  return {
    api: {} as WidgetContext['api'],
    canWrite: true,
    navigate: vi.fn(),
    theme: 'dark',
    confirm: async (message) => {
      confirmations.push(message)
      return true
    },
    write: async (point, value, priority) => {
      writes.push({ point, value, priority })
    },
    pointInfo: async () => null,
    ...overrides,
  }
}

function widget(type: WidgetType, overrides: Partial<WidgetDef> = {}): WidgetDef {
  const defaults = MODULES[type].defaults() as WidgetDef
  const { bind: extraBind, ...others } = overrides
  return { ...defaults, id: 'w1', type, ...others, bind: { ...defaults.bind, ...(extraBind ?? {}) } }
}

function mount(def: WidgetDef, ctx = context()): { instance: WidgetInstance; root: HTMLElement } {
  const instance = MODULES[def.type].render(container, def, ctx)
  instances.push(instance)
  return { instance, root: [...container.querySelectorAll<HTMLElement>('.w')].at(-1)! }
}

const sample = (value: number | null, status = 'ok'): LiveSample => ({ point: POINT, ts: 0, value, status })
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

beforeEach(() => {
  document.body.innerHTML = ''
  container = document.createElement('div')
  document.body.append(container)
  instances = []
  writes = []
  confirmations = []
})
afterEach(() => instances.forEach((i) => i.destroy()))

describe('registre', () => {
  it('fournit les 11 widgets de la spécification, chacun avec un éditeur et des valeurs par défaut sûres', () => {
    expect(Object.keys(MODULES).sort()).toEqual(['alarm_list', 'gauge', 'image', 'indicator', 'label', 'link', 'setpoint', 'shape', 'switch', 'trend', 'value'])
    for (const module of Object.values(MODULES)) {
      const defaults = module.defaults() as WidgetDef
      expect(defaults.w).toBeGreaterThan(0)
      expect(defaults.h).toBeGreaterThan(0)
      // Jamais de point vide par défaut : l'API refuserait un identifiant invalide.
      expect(defaults.bind.point).toBeUndefined()
      expect(module.editorSchema.length + module.label.length).toBeGreaterThan(0)
    }
  })
})

describe('formatNumber', () => {
  it('suit le format demandé', () => {
    expect(formatNumber(21.456, '0')).toBe('21')
    expect(formatNumber(21.456, '0.0')).toBe('21,5')
    expect(formatNumber(21.4, '0.00')).toBe('21,40')
    expect(formatNumber(1234.5, '#,##0.0')).toBe('1 234,5')
    expect(formatNumber(1234.5, '0.0')).toBe('1234,5')
    expect(formatNumber(21.456)).toBe('21,46') // sans format : lisible
    expect(formatNumber(null, '0.0')).toBe('—')
    expect(formatNumber(Number.NaN)).toBe('—')
  })
})

describe('value', () => {
  it('affiche la valeur formatée avec son unité', async () => {
    const info: PointInfo = { id: POINT, name: 'Temp', unit: '°C', path: null }
    const { instance, root } = mount(widget('value', { bind: { point: POINT, format: '0.0', unit: true } }), context({ pointInfo: async () => info }))
    expect(root.querySelector('.number')?.textContent).toContain('—')
    await flush()
    instance.update(sample(21.46))
    expect(root.querySelector('.number span')?.textContent).toBe('21,5')
    expect(root.querySelector('.unit')?.textContent).toBe('°C')
  })

  it("n'affiche pas l'unité quand elle est désactivée, et montre un tiret sans valeur", async () => {
    const info: PointInfo = { id: POINT, name: 'Temp', unit: '°C', path: null }
    const { instance, root } = mount(widget('value', { bind: { point: POINT, unit: false } }), context({ pointInfo: async () => info }))
    await flush()
    instance.update(sample(20))
    expect(root.querySelector('.unit')?.textContent).toBe('')
    instance.update(sample(20, 'comm_lost'))
    expect(root.querySelector('.number span')?.textContent).toBe('—') // pas de zéro trompeur
    expect(root.dataset.status).toBe('comm_lost')
  })

  it("applique les règles d'affichage, la dernière vraie l'emporte, puis les retire", () => {
    const def = widget('value', {
      bind: { point: POINT },
      style: { color: '#ffffff', fontSize: 24 },
      rules: [
        { when: 'value > 28', style: { color: '#ef4444' } },
        { when: "status != 'ok'", style: { opacity: 0.4 } },
        { when: 'value > 35', style: { color: '#7f1d1d' } },
      ],
    })
    const { instance, root } = mount(def)
    expect(root.style.color).toBe('rgb(255, 255, 255)')
    instance.update(sample(30))
    expect(root.style.color).toBe('rgb(239, 68, 68)')
    instance.update(sample(40))
    expect(root.style.color).toBe('rgb(127, 29, 29)') // plusieurs règles vraies : la dernière gagne
    instance.update(sample(20, 'fault'))
    expect(root.style.color).toBe('rgb(255, 255, 255)') // retour au style de base
    expect(root.style.opacity).toBe('0.4')
    instance.update(sample(20, 'ok'))
    expect(root.style.opacity).toBe('') // l'opacité posée par la règle est retirée
    expect(root.style.fontSize).toBe('24px')
  })

  it("ignore une règle invalide ou un style dangereux au lieu de casser l'affichage", () => {
    const def = widget('value', {
      bind: { point: POINT },
      rules: [
        { when: 'value >', style: { color: 'red' } },
        { when: 'value > 0', style: { color: 'url(https://evil/x)', background: 'red; position:fixed' } },
      ],
    })
    const { instance, root } = mount(def)
    expect(() => instance.update(sample(5))).not.toThrow()
    expect(root.style.color).not.toContain('url')
    expect(root.style.background).toBe('')
    expect(root.style.position).toBe('')
  })

  it('montre un libellé en texte, jamais en HTML', () => {
    const { root } = mount(widget('value', { label: '<img src=x onerror="window.pwned=1">' }))
    expect(root.querySelector('img')).toBeNull()
    expect(root.querySelector('.caption')?.textContent).toContain('<img')
  })
})

describe('label', () => {
  it('affiche le texte tel quel, aligné', () => {
    const { root } = mount(widget('label', { text: 'CTA 1 <b>gras</b>', style: { textAlign: 'center', fontSize: 20 } }))
    expect(root.textContent).toBe('CTA 1 <b>gras</b>')
    expect(root.querySelector('b')).toBeNull()
    expect(root.style.getPropertyValue('--w-text-align-flex')).toBe('center')
  })
})

describe('gauge', () => {
  it("remplit l'arc proportionnellement entre min et max, bornée", () => {
    const { instance, root } = mount(widget('gauge', { bind: { point: POINT, format: '0' }, min: 0, max: 200 }))
    const fill = root.querySelector('.fill')!
    instance.update(sample(50))
    expect(fill.getAttribute('stroke-dasharray')).toBe('25.0 100')
    instance.update(sample(500))
    expect(fill.getAttribute('stroke-dasharray')).toBe('100.0 100') // borné à max
    instance.update(sample(-20))
    expect(fill.getAttribute('stroke-dasharray')).toBe('0.0 100')
    expect(root.querySelector('.val')?.textContent).toBe('-20')
    instance.update(sample(null, 'comm_lost'))
    expect(root.querySelector('.val')?.textContent).toBe('—')
    expect(root.querySelector('svg')?.getAttribute('aria-label')).toContain('de 0 à 200')
  })

  it('supporte des bornes négatives et une plage dégénérée', () => {
    const { instance, root } = mount(widget('gauge', { bind: { point: POINT }, min: -10, max: 10 }))
    instance.update(sample(0))
    expect(root.querySelector('.fill')?.getAttribute('stroke-dasharray')).toBe('50.0 100')
    const degenerate = mount(widget('gauge', { bind: { point: POINT }, min: 5, max: 5 }))
    degenerate.instance.update(sample(5))
    expect(degenerate.root.querySelector('.fill')?.getAttribute('stroke-dasharray')).toBe('0.0 100')
  })
})

describe('indicator', () => {
  it("montre l'état d'un binaire en couleur ET en texte", () => {
    const { instance, root } = mount(widget('indicator', { bind: { point: POINT } }))
    instance.update(sample(1))
    expect(root.querySelector('.text')?.textContent).toBe('Marche')
    expect(root.querySelector<HTMLElement>('.lamp')?.style.getPropertyValue('--lamp')).toBe('#22c55e')
    instance.update(sample(0))
    expect(root.querySelector('.text')?.textContent).toBe('Arrêt')
    instance.update(sample(null, 'comm_lost'))
    expect(root.querySelector('.text')?.textContent).toBe('Inconnu')
    expect(root.getAttribute('role')).toBe('status')
  })

  it('utilise les libellés du multi-état, puis des états configurés', async () => {
    const info: PointInfo = { id: POINT, name: 'Mode', unit: null, path: null, state_text: ['Arrêt', 'Réduit', 'Confort'] }
    const multi = mount(widget('indicator', { bind: { point: POINT } }), context({ pointInfo: async () => info }))
    await flush()
    multi.instance.update(sample(3))
    expect(multi.root.querySelector('.text')?.textContent).toBe('Confort')
    multi.instance.update(sample(9))
    expect(multi.root.querySelector('.text')?.textContent).toBe('État 9')

    const custom = mount(widget('indicator', { bind: { point: POINT }, states: [{ value: 2, label: 'Défaut', color: '#ef4444' }] }))
    custom.instance.update(sample(2))
    expect(custom.root.querySelector('.text')?.textContent).toBe('Défaut')
    expect(custom.root.querySelector<HTMLElement>('.lamp')?.style.getPropertyValue('--lamp')).toBe('#ef4444')
  })

  it('peut masquer le texte sans perdre le nom accessible', () => {
    const { instance, root } = mount(widget('indicator', { bind: { point: POINT }, showLabel: false }))
    instance.update(sample(1))
    expect(root.querySelector<HTMLElement>('.text')?.hidden).toBe(true)
    expect(root.getAttribute('aria-label')).toBe('Marche')
  })
})

describe('switch', () => {
  const button = () => container.querySelector<HTMLButtonElement>('button')!

  it("reflète l'état et écrit la valeur opposée avec la priorité, après confirmation", async () => {
    const { instance } = mount(widget('switch', { bind: { point: POINT }, label: 'Ventilateur', priority: 10 }))
    instance.update(sample(0))
    expect(button().getAttribute('aria-checked')).toBe('false')
    button().click()
    await flush()
    expect(confirmations).toEqual(['Ventilateur : passer à « Marche » ?'])
    expect(writes).toEqual([{ point: POINT, value: 1, priority: 10 }])

    instance.update(sample(1))
    expect(button().getAttribute('aria-checked')).toBe('true')
    button().click()
    await flush()
    expect(writes.at(-1)).toEqual({ point: POINT, value: 0, priority: 10 })
  })

  it("n'écrit rien si l'opérateur refuse la confirmation", async () => {
    const { instance } = mount(widget('switch', { bind: { point: POINT } }), context({ confirm: async () => false }))
    instance.update(sample(0))
    button().click()
    await flush()
    expect(writes).toEqual([])
  })

  it('écrit directement quand la confirmation est désactivée', async () => {
    const { instance } = mount(widget('switch', { bind: { point: POINT }, confirm: false }))
    instance.update(sample(0))
    button().click()
    await flush()
    expect(confirmations).toEqual([])
    expect(writes).toHaveLength(1)
  })

  it('est désactivé sans droits, sans point lié ou dans l\'éditeur', () => {
    mount(widget('switch', { bind: { point: POINT } }), context({ canWrite: false }))
    expect(button().disabled).toBe(true)
    expect(button().title).toMatch(/Droits insuffisants/)
    container.innerHTML = ''
    mount(widget('switch'))
    expect(button().disabled).toBe(true)
    expect(button().title).toBe('Aucun point lié')
    container.innerHTML = ''
    mount(widget('switch', { bind: { point: POINT } }), context({ design: true }))
    expect(button().disabled).toBe(true)
  })

  it("montre l'échec de la commande et empêche le double clic pendant l'envoi", async () => {
    let fail: (error: Error) => void = () => undefined
    const write = vi.fn(() => new Promise<void>((_resolve, reject) => (fail = reject)))
    const { instance } = mount(widget('switch', { bind: { point: POINT }, confirm: false }), context({ write }))
    instance.update(sample(0))
    button().click()
    await flush()
    expect(button().disabled).toBe(true)
    button().click()
    expect(write).toHaveBeenCalledTimes(1)
    fail(new Error('point non inscriptible'))
    await flush()
    expect(container.querySelector('.error')?.textContent).toBe('Échec : point non inscriptible')
    expect(button().disabled).toBe(false) // on peut réessayer
  })

  it("affiche « inconnu » quand la valeur est perdue", () => {
    const { instance, root } = mount(widget('switch', { bind: { point: POINT } }))
    instance.update(sample(null, 'comm_lost'))
    expect(root.dataset.state).toBe('unknown')
    expect(container.querySelector('.text')?.textContent).toContain('inconnu')
  })
})

describe('setpoint', () => {
  const input = () => container.querySelector<HTMLInputElement>('input')!
  const buttons = () => [...container.querySelectorAll<HTMLButtonElement>('button')]
  const applyButton = () => buttons().find((b) => b.textContent === 'Appliquer')!

  it("envoie la consigne saisie avec la priorité configurée", async () => {
    mount(widget('setpoint', { bind: { point: POINT }, priority: 12, min: 10, max: 30 }))
    input().value = '22.5'
    applyButton().click()
    await flush()
    expect(writes).toEqual([{ point: POINT, value: 22.5, priority: 12 }])
    expect(container.querySelector('.msg')?.textContent).toBe('Consigne envoyée')
  })

  it("applique avec la touche Entrée", async () => {
    mount(widget('setpoint', { bind: { point: POINT } }))
    input().value = '19'
    input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    await flush()
    expect(writes).toHaveLength(1)
  })

  it('refuse une valeur hors bornes, sans jamais écrire', async () => {
    mount(widget('setpoint', { bind: { point: POINT }, min: 10, max: 30 }))
    for (const [value, message] of [['5', 'Minimum'], ['31', 'Maximum']] as const) {
      input().value = value
      applyButton().click()
      await flush()
      expect(container.querySelector('.msg')?.textContent).toContain(message)
      expect(container.querySelector('.msg')?.classList.contains('error')).toBe(true)
    }
    input().value = ''
    applyButton().click()
    expect(container.querySelector('.msg')?.textContent).toBe('Saisissez un nombre')
    expect(writes).toEqual([])
    expect(input().min).toBe('10')
    expect(input().max).toBe('30')
  })

  it('reprend les bornes du point quand le widget n\'en fixe pas', async () => {
    const info: PointInfo = { id: POINT, name: 'Consigne', unit: '°C', path: null, write_min: 15, write_max: 25 }
    mount(widget('setpoint', { bind: { point: POINT } }), context({ pointInfo: async () => info }))
    await flush()
    input().value = '30'
    applyButton().click()
    await flush()
    expect(container.querySelector('.msg')?.textContent).toBe('Maximum : 25 °C')
    expect(writes).toEqual([])
  })

  it('suit la valeur en vigueur sauf pendant la saisie', () => {
    const { instance } = mount(widget('setpoint', { bind: { point: POINT } }))
    instance.update(sample(21))
    expect(input().value).toBe('21')
    input().focus()
    input().value = '23'
    instance.update(sample(21))
    expect(input().value).toBe('23') // la saisie n'est pas écrasée
  })

  it("relâche la priorité avec le bouton dédié, s'il est activé", async () => {
    mount(widget('setpoint', { bind: { point: POINT }, allowRelease: true, priority: 9 }))
    buttons().find((b) => b.textContent === 'Relâcher')!.click()
    await flush()
    expect(writes).toEqual([{ point: POINT, value: null, priority: 9 }])
    container.innerHTML = ''
    mount(widget('setpoint', { bind: { point: POINT } }))
    expect(buttons().find((b) => b.textContent === 'Relâcher')!.hidden).toBe(true)
  })

  it('demande confirmation si configuré, et signale les échecs', async () => {
    const write = vi.fn().mockRejectedValue(new Error('priorité occupée'))
    mount(widget('setpoint', { bind: { point: POINT }, confirm: true, label: 'Consigne salle' }), context({ write }))
    input().value = '20'
    applyButton().click()
    await flush()
    expect(confirmations).toEqual(['Consigne salle : envoyer 20 ?'])
    expect(container.querySelector('.msg')?.textContent).toBe('Échec : priorité occupée')
  })

  it('est désactivé sans droits', () => {
    mount(widget('setpoint', { bind: { point: POINT } }), context({ canWrite: false }))
    expect(input().disabled).toBe(true)
    expect(applyButton().disabled).toBe(true)
  })
})

describe('image', () => {
  const png = 'data:image/png;base64,iVBORw0KGgo='

  it('affiche une image en data URL et refuse tout le reste', () => {
    mount(widget('image', { src: png, alt: 'CTA' }))
    const image = container.querySelector('img')!
    expect(image.getAttribute('src')).toBe(png)
    expect(image.alt).toBe('CTA')
    container.innerHTML = ''
    for (const hostile of ['https://evil/x.png', 'javascript:alert(1)', 'data:text/html;base64,PGh0bWw+', '']) {
      mount(widget('image', { src: hostile }))
      expect(container.querySelector('img')).toBeNull()
      expect(container.textContent).toBe('Aucune image')
      container.innerHTML = ''
    }
  })
})

describe('link', () => {
  it('navigue vers la cible au clic', () => {
    const navigate = vi.fn()
    mount(widget('link', { text: 'CTA 2', target: 'synoptic:cta-2' }), context({ navigate }))
    const button = container.querySelector('button')!
    expect(button.textContent).toBe('CTA 2')
    button.click()
    expect(navigate).toHaveBeenCalledWith('synoptic:cta-2')
  })

  it("n'a pas d'effet sans cible ni dans l'éditeur", () => {
    const navigate = vi.fn()
    mount(widget('link', { target: '' }), context({ navigate }))
    expect(container.querySelector('button')!.disabled).toBe(true)
    container.innerHTML = ''
    mount(widget('link', { target: 'synoptic:x' }), context({ navigate, design: true }))
    container.querySelector('button')!.click()
    expect(navigate).not.toHaveBeenCalled()
  })
})

describe('shape', () => {
  it('dessine un rectangle, une ligne et un tuyau', () => {
    mount(widget('shape', { shape: 'rect', w: 100, h: 60 }))
    expect(container.querySelector('rect.shape')).not.toBeNull()
    container.innerHTML = ''
    mount(widget('shape', { shape: 'line', w: 200, h: 10 }))
    expect(container.querySelector('path.shape')?.getAttribute('d')).toBe('M2 50 L98 50')
    container.innerHTML = ''
    mount(widget('shape', { shape: 'line', w: 10, h: 200 }))
    expect(container.querySelector('path.shape')?.getAttribute('d')).toBe('M50 2 L50 98') // vertical
    container.innerHTML = ''
    mount(widget('shape', { shape: 'pipe' }))
    expect(container.querySelector('path.pipe')).not.toBeNull()
    expect(container.querySelector('path.flow')).not.toBeNull()
  })

  it("anime l'écoulement d'un tuyau tant que la valeur liée est positive, et change de couleur par règle", () => {
    const { instance, root } = mount(
      widget('shape', {
        shape: 'pipe',
        flow: true,
        bind: { point: POINT },
        style: { stroke: '#38bdf8' },
        rules: [{ when: 'value <= 0', style: { stroke: '#64748b' } }],
      }),
    )
    expect(root.dataset.flow).toBe('off')
    instance.update(sample(12))
    expect(root.dataset.flow).toBe('on')
    expect(root.style.getPropertyValue('--w-stroke')).toBe('#38bdf8')
    instance.update(sample(0))
    expect(root.dataset.flow).toBe('off')
    expect(root.style.getPropertyValue('--w-stroke')).toBe('#64748b')
    instance.update(sample(null, 'comm_lost'))
    expect(root.dataset.flow).toBe('off')
  })

  it("anime toujours un tuyau sans point lié quand l'écoulement est activé", () => {
    const { root } = mount(widget('shape', { shape: 'pipe', flow: true }))
    expect(root.dataset.flow).toBe('on')
    container.innerHTML = ''
    const still = mount(widget('shape', { shape: 'pipe', flow: false }))
    expect(still.root.dataset.flow).toBe('off')
  })
})

describe('trend et alarm_list dans un synoptique', () => {
  it("affichent un repère dans l'éditeur, sans réseau", () => {
    for (const type of ['trend', 'alarm_list'] as const) {
      container.innerHTML = ''
      mount(widget(type), context({ design: true }))
      expect(container.querySelector('.w-placeholder')).not.toBeNull()
    }
  })

  it('signalent une configuration invalide sans casser la page', () => {
    mount(widget('trend'), context()) // aucun point lié
    expect(container.querySelector('.w-placeholder')?.textContent).toMatch(/au moins un point/)
  })
})
