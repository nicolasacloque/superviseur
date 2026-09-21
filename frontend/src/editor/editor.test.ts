import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { AlarmEvent, LiveApi, LiveSample, LiveState, PointInfo, SynopticRecord, SynopticSummary } from '../api/types'
import { newDoc, type SynopticDoc } from '../synoptic/model'
import { type EditorApi, type EditorInstance, mountEditor } from './editor'

const P1 = '11111111-1111-4111-8111-111111111111'

class FakeLive implements LiveApi {
  state: LiveState = 'open'
  subscribe(_points: string[], _listener: (s: LiveSample) => void): () => void {
    return () => undefined
  }
  subscribeAlarms(_listener: (e: AlarmEvent) => void): () => void {
    return () => undefined
  }
  onState(): () => void {
    return () => undefined
  }
}

let container: HTMLElement
let editors: EditorInstance[]
let saved: SynopticRecord[]
let failWith: Error | null
let api: EditorApi & { created: SynopticDoc[]; puts: { id: string; base: number | undefined }[] }
let exited: number

function makeApi() {
  const created: SynopticDoc[] = []
  const puts: { id: string; base: number | undefined }[] = []
  const record = (doc: SynopticDoc, version: number): SynopticRecord => ({
    id: 'syn-1', name: doc.name, slug: 'cta-1', version, updated_at: '2026-09-21T10:00:00Z', doc,
  })  // fmt: skip
  return {
    created,
    puts,
    history: async () => ({ point_id: '', bucket: null, truncated: false, items: [] }),
    alarms: async () => ({ items: [], total: 0 }),
    acknowledge: async () => { throw new Error('non utilisé') },
    point: async (id: string): Promise<PointInfo> => ({ id, name: 'Temp', unit: '°C', path: null }),
    write: async () => undefined,
    tree: async () => ({ path: '', folders: [], points: [] }),
    search: async () => [],
    synoptics: async (): Promise<SynopticSummary[]> => [],
    synoptic: async () => { throw new Error('non utilisé') },
    createSynoptic: async (doc: SynopticDoc) => {
      if (failWith) throw failWith
      created.push(doc)
      return record(doc, 1)
    },
    saveSynoptic: async (id: string, doc: SynopticDoc, base?: number) => {
      if (failWith) throw failWith
      puts.push({ id, base })
      return record(doc, (base ?? 0) + 1)
    },
    deleteSynoptic: async () => undefined,
    versions: async () => [],
    version: async () => { throw new Error('non utilisé') },
    restoreVersion: async () => { throw new Error('non utilisé') },
  } as unknown as typeof api  // fmt: skip
}

function open(record: SynopticRecord | null = null): EditorInstance {
  const editor = mountEditor(container, {
    api, live: new FakeLive(), role: 'engineer', record,
    exit: () => { exited++ },
    onSaved: (r) => saved.push(r),
    confirm: async () => true,
  })  // fmt: skip
  editors.push(editor)
  return editor
}

const paletteButton = (type: string) => container.querySelector<HTMLButtonElement>(`.ed-palette__item[data-type="${type}"]`)!
const items = () => [...container.querySelectorAll<HTMLElement>('.ed-item')]
const hit = (index = 0) => items()[index]!.querySelector<HTMLElement>('.ed-hit')!
const status = () => container.querySelector('.ed-status')!.textContent ?? ''
const saveButton = () => [...container.querySelectorAll('button')].find((b) => b.textContent === 'Enregistrer')!

function pointer(target: EventTarget, type: string, x: number, y: number, init: MouseEventInit = {}): void {
  target.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0, ...init }))
}
function drag(from: HTMLElement, x0: number, y0: number, x1: number, y1: number, init: MouseEventInit = {}): void {
  pointer(from, 'pointerdown', x0, y0, init)
  pointer(window, 'pointermove', x1, y1, init)
  pointer(window, 'pointerup', x1, y1, init)
}
const key = (k: string, init: KeyboardEventInit = {}) =>
  document.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, ...init }))

beforeEach(() => {
  document.body.innerHTML = ''
  container = document.createElement('div')
  document.body.append(container)
  editors = []
  saved = []
  failWith = null
  exited = 0
  api = makeApi()
})
afterEach(() => editors.forEach((e) => e.destroy()))

describe('palette', () => {
  it('propose les 11 types de widget', () => {
    open()
    expect(container.querySelectorAll('.ed-palette__item')).toHaveLength(11)
  })

  it('un clic ajoute un widget sélectionné', () => {
    const editor = open()
    paletteButton('value').click()
    expect(editor.state.doc.widgets).toHaveLength(1)
    expect(items()).toHaveLength(1)
    expect(items()[0]!.classList.contains('is-selected')).toBe(true)
    expect(container.querySelector('.ed-selbox')).not.toBeNull()
  })

  it('le glisser-déposer place le widget sous le curseur', () => {
    const editor = open()
    const canvas = container.querySelector('.ed-canvas')!
    const event = new Event('drop', { bubbles: true, cancelable: true }) as Event & { clientX: number; clientY: number; dataTransfer: unknown }
    Object.assign(event, { clientX: 300, clientY: 200, dataTransfer: { getData: () => 'label' } })
    canvas.dispatchEvent(event)
    const w = editor.state.doc.widgets[0]!
    expect(w.type).toBe('label')
    expect(w.x).toBeGreaterThan(100)
    expect(w.x).toBeLessThanOrEqual(300)
    expect(w.y).toBeGreaterThan(50)
    expect(w.y).toBeLessThanOrEqual(200)
  })
})

describe('manipulation', () => {
  it('déplace un widget en le glissant (magnétisme à la grille)', () => {
    const editor = open()
    paletteButton('value').click()
    const w = editor.state.doc.widgets[0]!
    const { x, y } = w
    drag(hit(), 500, 500, 543, 527)
    expect(w.x % 10).toBe(0)
    expect(w.x).toBeGreaterThan(x + 30)
    expect(w.y).toBeGreaterThan(y + 20)
    // un seul pas d'annulation pour tout le geste
    editor.state.undo()
    const restored = editor.state.doc.widgets[0]! // l'annulation remplace le document
    expect([restored.x, restored.y]).toEqual([x, y])
  })

  it('un simple clic ne déplace rien', () => {
    const editor = open()
    paletteButton('value').click()
    const w = editor.state.doc.widgets[0]!
    const before = { x: w.x, y: w.y }
    drag(hit(), 500, 500, 501, 500)
    expect({ x: w.x, y: w.y }).toEqual(before)
  })

  it('redimensionne par la poignée', () => {
    const editor = open()
    paletteButton('value').click()
    const w = editor.state.doc.widgets[0]!
    const width = w.w
    const handle = container.querySelector<HTMLElement>('[data-handle="e"]')!
    drag(handle, 100, 100, 160, 100)
    expect(w.w).toBeGreaterThan(width + 40)
  })

  it('sélectionne par rectangle', () => {
    const editor = open()
    paletteButton('value').click()
    paletteButton('label').click()
    editor.state.clearSelection()
    const canvas = container.querySelector<HTMLElement>('.ed-canvas')!
    drag(canvas, 0, 0, 1900, 1000)
    expect(editor.state.selection.size).toBe(2)
  })

  it('un clic dans le vide désélectionne', () => {
    const editor = open()
    paletteButton('value').click()
    const canvas = container.querySelector<HTMLElement>('.ed-canvas')!
    drag(canvas, 1800, 1000, 1800, 1000)
    expect(editor.state.selection.size).toBe(0)
  })

  it('Maj+clic ajoute à la sélection', () => {
    const editor = open()
    paletteButton('value').click()
    paletteButton('label').click()
    editor.state.select([editor.state.doc.widgets[0]!.id])
    pointer(hit(1), 'pointerdown', 0, 0, { shiftKey: true })
    pointer(window, 'pointerup', 0, 0)
    expect(editor.state.selection.size).toBe(2)
  })
})

describe('clavier', () => {
  it('Suppr supprime, Ctrl+Z rétablit', () => {
    const editor = open()
    paletteButton('value').click()
    key('Delete')
    expect(editor.state.doc.widgets).toHaveLength(0)
    expect(items()).toHaveLength(0)
    key('z', { ctrlKey: true })
    expect(editor.state.doc.widgets).toHaveLength(1)
    key('z', { ctrlKey: true, shiftKey: true })
    expect(editor.state.doc.widgets).toHaveLength(0)
  })

  it('les flèches déplacent de 1 px, Maj de 10 px', () => {
    const editor = open()
    paletteButton('value').click()
    const w = editor.state.doc.widgets[0]!
    const x = w.x
    key('ArrowRight')
    expect(w.x).toBe(x + 1)
    key('ArrowRight', { shiftKey: true })
    expect(w.x).toBe(x + 11)
  })

  it('Ctrl+D duplique, Ctrl+A sélectionne tout', () => {
    const editor = open()
    paletteButton('value').click()
    key('d', { ctrlKey: true })
    expect(editor.state.doc.widgets).toHaveLength(2)
    editor.state.clearSelection()
    key('a', { ctrlKey: true })
    expect(editor.state.selection.size).toBe(2)
  })

  it('ignore les raccourcis pendant la saisie dans un champ', () => {
    const editor = open()
    paletteButton('value').click()
    const input = container.querySelector<HTMLInputElement>('.ed-name')!
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }))
    expect(editor.state.doc.widgets).toHaveLength(1)
  })
})

describe('enregistrement', () => {
  it('crée puis met à jour avec la version de base', async () => {
    const editor = open()
    paletteButton('value').click()
    expect(editor.state.dirty).toBe(true)
    await editor.save()
    expect(api.created).toHaveLength(1)
    expect(saved[0]!.version).toBe(1)
    expect(editor.state.dirty).toBe(false)
    expect(status()).toContain('Version 1 enregistrée')

    paletteButton('label').click()
    await editor.save()
    expect(api.puts).toEqual([{ id: 'syn-1', base: 1 }])
    expect(saved[1]!.version).toBe(2)
  })

  it('Ctrl+S enregistre', async () => {
    open()
    paletteButton('value').click()
    key('s', { ctrlKey: true })
    await vi.waitFor(() => expect(api.created).toHaveLength(1))
  })

  it('signale un conflit de version', async () => {
    const rec: SynopticRecord = { id: 'syn-1', name: 'CTA 1', slug: 'cta-1', version: 3, updated_at: '', doc: newDoc('CTA 1') }
    const editor = open(rec)
    paletteButton('value').click()
    failWith = new ApiError(409, 'version obsolète')
    await editor.save()
    expect(status()).toContain('Conflit')
    expect(editor.state.dirty).toBe(true)
    expect(saved).toHaveLength(0)
  })

  it('affiche l\'erreur de validation renvoyée par le serveur', async () => {
    const editor = open()
    paletteButton('value').click()
    failWith = new ApiError(422, 'widgets > 0 : type inconnu')
    await editor.save()
    expect(status()).toContain('Échec')
    expect(status()).toContain('type inconnu')
    expect(saveButton().disabled).toBe(false)
  })

  it('le nom du synoptique est modifiable', () => {
    const editor = open()
    const input = container.querySelector<HTMLInputElement>('.ed-name')!
    input.value = 'Mon synoptique'
    input.dispatchEvent(new Event('input'))
    expect(editor.state.doc.name).toBe('Mon synoptique')
  })

  it('quitter avec des modifications demande confirmation', async () => {
    open()
    paletteButton('value').click()
    const back = [...container.querySelectorAll('button')].find((b) => b.textContent?.includes('Synoptiques'))!
    back.click()
    await vi.waitFor(() => expect(exited).toBe(1))
  })

  it('refuse de quitter si la confirmation est déclinée', async () => {
    const editor = mountEditor(container, {
      api, live: new FakeLive(), role: 'engineer', record: null, exit: () => { exited++ }, confirm: async () => false,
    })  // fmt: skip
    editors.push(editor)
    paletteButton('value').click()
    ;[...container.querySelectorAll('button')].find((b) => b.textContent?.includes('Synoptiques'))!.click()
    await new Promise((r) => setTimeout(r, 0))
    expect(exited).toBe(0)
  })
})

describe('aperçu et zoom', () => {
  it("l'aperçu affiche le viewer puis revient à l'édition", () => {
    const editor = open()
    paletteButton('label').click()
    const toggle = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Aperçu en direct')!
    toggle.click()
    expect(container.querySelector('.ed-preview .viewer')).not.toBeNull()
    expect(container.querySelector<HTMLElement>('.ed-stage')!.hidden).toBe(true)
    expect(editor.state.doc.widgets).toHaveLength(1)
    key('Escape')
    expect(container.querySelector('.ed-preview .viewer')).toBeNull()
    expect(container.querySelector<HTMLElement>('.ed-stage')!.hidden).toBe(false)
  })

  it('le zoom modifie l\'échelle du canevas', () => {
    open()
    const canvas = container.querySelector<HTMLElement>('.ed-canvas')!
    const zoomIn = container.querySelector<HTMLButtonElement>('button[aria-label="Agrandir"]')!
    zoomIn.click()
    expect(canvas.style.transform).toBe('scale(1.25)')
    expect(container.querySelector('.ed-zoom')!.textContent).toBe('125 %')
  })

  it('la grille peut être masquée', () => {
    open()
    const grid = container.querySelector<HTMLElement>('.ed-grid-layer')!
    const box = [...container.querySelectorAll<HTMLInputElement>('input[type=checkbox]')][1]!
    box.checked = false
    box.dispatchEvent(new Event('change'))
    expect(grid.hidden).toBe(true)
  })
})

describe('cycle de vie', () => {
  it('destroy retire tout et détache le clavier', () => {
    const editor = open()
    paletteButton('value').click()
    editor.destroy()
    expect(container.children).toHaveLength(0)
    key('Delete') // ne doit rien casser
    void P1
  })

  it('charge un synoptique existant', () => {
    const doc = newDoc('CTA 1')
    const rec: SynopticRecord = { id: 'syn-1', name: 'CTA 1', slug: 'cta-1', version: 4, updated_at: '', doc }
    const editor = open(rec)
    expect(editor.state.dirty).toBe(false)
    expect(status()).toContain('Version 4 enregistrée')
    expect(container.querySelector<HTMLInputElement>('.ed-name')!.value).toBe('CTA 1')
  })
})
