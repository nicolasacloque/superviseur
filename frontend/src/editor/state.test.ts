import { beforeEach, describe, expect, it, vi } from 'vitest'
import { newDoc, type SynopticDoc } from '../synoptic/model'
import { COALESCE_MS, EditorState, HISTORY_LIMIT, PASTE_OFFSET } from './state'

let clock: number
let state: EditorState

const make = (doc: SynopticDoc = newDoc('Test')) => new EditorState(doc, () => clock)
const ids = () => state.doc.widgets.map((w) => w.id)
const pos = (id: string) => [state.widget(id)!.x, state.widget(id)!.y]

beforeEach(() => {
  clock = 0
  state = make()
})

describe('ajout de widgets', () => {
  it('crée un widget avec ses valeurs par défaut, un identifiant unique, et le sélectionne', () => {
    const value = state.addWidget('value')
    const gauge = state.addWidget('gauge')
    expect(ids()).toEqual(['w1', 'w2'])
    expect(value.type).toBe('value')
    expect(value.bind.point).toBeUndefined() // jamais de point vide
    expect([...state.selection]).toEqual([gauge.id])
    expect(state.dirty).toBe(true)
  })

  it('centre le widget sur le point de dépôt et cale sur la grille', () => {
    const widget = state.addWidget('value', { x: 500, y: 300 }) // 160 x 56
    expect([widget.x, widget.y]).toEqual([420, 270]) // 500-80, 300-28 -> grille de 10
    state.setGrid(10, false)
    const free = state.addWidget('value', { x: 503, y: 301 })
    expect([free.x, free.y]).toEqual([423, 273]) // sans magnétisme : arrondi seulement
  })

  it('ne réutilise jamais un identifiant après suppression', () => {
    state.addWidget('value')
    state.addWidget('value')
    state.select(['w1'])
    state.deleteSelected()
    state.addWidget('value')
    expect(new Set(ids()).size).toBe(ids().length)
  })

  it('décale les widgets ajoutés sans position pour ne pas les empiler', () => {
    state.addWidget('label')
    state.addWidget('label')
    expect(pos('w1')).not.toEqual(pos('w2'))
  })
})

describe('sélection', () => {
  beforeEach(() => {
    for (let i = 0; i < 3; i++) state.addWidget('label', { x: 100 + i * 300, y: 100 })
  })

  it('sélectionne, ajoute, bascule et efface', () => {
    state.select(['w1'])
    state.select(['w2'], true)
    expect([...state.selection].sort()).toEqual(['w1', 'w2'])
    state.toggle('w1')
    expect([...state.selection]).toEqual(['w2'])
    state.select(['w3']) // sans ajout : remplace
    expect([...state.selection]).toEqual(['w3'])
    state.clearSelection()
    expect(state.selection.size).toBe(0)
    state.select(['absent'])
    expect(state.selection.size).toBe(0)
  })

  it('sélectionne tout et par zone', () => {
    state.selectAll()
    expect(state.selection.size).toBe(3)
    state.selectInRect({ x: 0, y: 0, w: 250, h: 400 })
    expect([...state.selection]).toEqual(['w1'])
    state.selectInRect({ x: 350, y: 0, w: 100, h: 400 }, true)
    expect([...state.selection].sort()).toEqual(['w1', 'w2'])
  })

  it("ne fait pas de sélection un motif d'annulation", () => {
    state.undo() // annule l'ajout du dernier widget, pas une sélection
    expect(ids()).toEqual(['w1', 'w2'])
  })
})

describe('déplacement, suppression, ordre', () => {
  it('déplace la sélection en bloc', () => {
    state.addWidget('label', { x: 100, y: 100 })
    state.addWidget('label', { x: 300, y: 100 })
    state.selectAll()
    const before = [pos('w1'), pos('w2')]
    state.moveSelected(25, -10)
    expect([pos('w1'), pos('w2')]).toEqual(before.map(([x, y]) => [x! + 25, y! - 10]))
  })

  it('cale le coin de la sélection sur la grille quand le magnétisme est actif', () => {
    state.addWidget('label', { x: 100, y: 100 })
    state.addWidget('label', { x: 300, y: 100 })
    state.selectAll()
    const [x0, y0] = pos('w1') as [number, number]
    state.moveSelected(13, 7, true)
    expect(pos('w1')).toEqual([Math.round((x0 + 13) / 10) * 10, Math.round((y0 + 7) / 10) * 10])
    expect(state.widget('w2')!.x - state.widget('w1')!.x).toBe(200) // l'écart entre widgets est conservé
  })

  it('supprime la sélection avec les liens qui la visent', () => {
    state.addWidget('link')
    state.addWidget('shape')
    state.setLink('w1', 'synoptic:cta-2')
    state.select(['w1'])
    state.deleteSelected()
    expect(ids()).toEqual(['w2'])
    expect(state.doc.links).toEqual([])
    expect(state.selection.size).toBe(0)
  })

  it("change l'ordre d'empilement", () => {
    for (let i = 0; i < 3; i++) state.addWidget('label')
    state.select(['w1'])
    state.bringToFront()
    expect(ids()).toEqual(['w2', 'w3', 'w1'])
    state.select(['w3'])
    state.sendToBack()
    expect(ids()).toEqual(['w3', 'w2', 'w1'])
  })
})

describe('placement absolu (glisser)', () => {
  it("place à origine + delta sans dérive, et cale le coin de la sélection", () => {
    state.addWidget('label', { x: 100, y: 100 })
    state.addWidget('label', { x: 400, y: 100 })
    state.selectAll()
    const origins = new Map(state.selected.map((w) => [w.id, { x: w.x, y: w.y }]))
    const [x1, y1] = [origins.get('w1')!.x, origins.get('w1')!.y]
    state.begin()
    for (const step of [3, 7, 13]) state.placeSelected(origins, step, step, true)
    state.end()
    expect(pos('w1')).toEqual([Math.round((x1 + 13) / 10) * 10, Math.round((y1 + 13) / 10) * 10])
    expect(state.widget('w2')!.x - state.widget('w1')!.x).toBe(300)
    state.undo() // les trois placements forment un seul geste : une annulation suffit
    expect(pos('w1')).toEqual([x1, y1])
    state.placeSelected(origins, 0, 0, false)
    expect(pos('w1')).toEqual([x1, y1]) // à l'origine exactement : aucune dérive cumulée
  })

  it("ignore les widgets qui ne sont pas dans les origines", () => {
    state.addWidget('label')
    state.placeSelected(new Map(), 50, 50, false)
    expect(pos('w1')).not.toEqual([50, 50])
  })
})

describe('alignement', () => {
  it('aligne et répartit la sélection', () => {
    state.addWidget('label', { x: 100, y: 100 })
    state.addWidget('label', { x: 400, y: 200 })
    state.addWidget('label', { x: 1000, y: 50 })
    state.selectAll()
    state.alignSelected('top')
    expect(new Set(state.doc.widgets.map((w) => w.y)).size).toBe(1)
    state.distributeSelected('horizontal')
    const xs = state.doc.widgets.map((w) => w.x)
    expect(xs[1]! - xs[0]! - state.doc.widgets[0]!.w).toBeCloseTo(xs[2]! - xs[1]! - state.doc.widgets[1]!.w)
  })

  it('ignore une sélection insuffisante', () => {
    state.addWidget('label')
    const before = JSON.stringify(state.doc)
    state.alignSelected('left')
    state.distributeSelected('vertical')
    expect(JSON.stringify(state.doc)).toBe(before)
    expect(state.canUndo).toBe(true) // seul l'ajout est annulable
    state.undo()
    expect(state.canUndo).toBe(false)
  })
})

describe('copier / coller', () => {
  beforeEach(() => {
    state.addWidget('value', { x: 200, y: 200 })
    state.setProperty('w1', 'bind.point', '11111111-1111-4111-8111-111111111111')
    state.setLink('w1', 'synoptic:cta-2')
  })

  it('colle des copies indépendantes, décalées, avec de nouveaux identifiants et leurs liens', () => {
    state.select(['w1'])
    state.copy()
    const [copy] = state.paste()
    expect(copy!.id).toBe('w2')
    expect([copy!.x, copy!.y]).toEqual([state.widget('w1')!.x + PASTE_OFFSET, state.widget('w1')!.y + PASTE_OFFSET])
    expect([...state.selection]).toEqual(['w2'])
    expect(state.linkOf('w2')).toBe('synoptic:cta-2')
    state.setProperty('w2', 'bind.point', '22222222-2222-4222-8222-222222222222')
    expect(state.widget('w1')!.bind.point).toBe('11111111-1111-4111-8111-111111111111') // copie profonde
  })

  it("ne superpose pas deux collages successifs", () => {
    state.select(['w1'])
    state.copy()
    state.paste()
    state.paste()
    const xs = state.doc.widgets.map((w) => w.x)
    expect(new Set(xs).size).toBe(3)
  })

  it('duplique et coupe', () => {
    state.select(['w1'])
    state.duplicate()
    expect(ids()).toEqual(['w1', 'w2'])
    state.select(['w2'])
    state.cut()
    expect(ids()).toEqual(['w1'])
    expect(state.canPaste).toBe(true)
    state.paste()
    expect(ids()).toEqual(['w1', 'w2'])
  })

  it("ne fait rien sans presse-papiers", () => {
    const fresh = make()
    expect(fresh.paste()).toEqual([])
    expect(fresh.canPaste).toBe(false)
  })
})

describe('propriétés', () => {
  beforeEach(() => {
    state.addWidget('value')
  })

  it('fixe des chemins imbriqués, les crée au besoin et les supprime avec undefined', () => {
    state.setProperty('w1', 'style.color', '#ef4444')
    state.setProperty('w1', 'bind.format', '0.00')
    state.setProperty('w1', 'label', 'Soufflage')
    expect(state.widget('w1')!.style.color).toBe('#ef4444')
    expect(state.widget('w1')!.bind.format).toBe('0.00')
    expect(state.widget('w1')!.label).toBe('Soufflage')
    state.setProperty('w1', 'style.color', undefined)
    state.setProperty('w1', 'label', '')
    expect('color' in state.widget('w1')!.style).toBe(false)
    expect('label' in state.widget('w1')!).toBe(false) // texte vidé : la propriété disparaît
    state.setProperty('w1', 'nouveau.chemin.profond', 3)
    expect((state.widget('w1') as { nouveau?: { chemin?: { profond?: number } } }).nouveau?.chemin?.profond).toBe(3)
  })

  it('gère nom, canevas, règles et lien', () => {
    state.setName('CTA 1')
    state.setCanvas({ width: 1280, height: 720, background: '#ffffff' })
    state.setRules('w1', [{ when: 'value > 1', style: { color: 'red' } }])
    state.setLink('w1', 'synoptic:x')
    state.setLink('w1', null)
    expect(state.doc.name).toBe('CTA 1')
    expect(state.doc.canvas).toMatchObject({ width: 1280, height: 720, background: '#ffffff' })
    expect(state.widget('w1')!.rules).toHaveLength(1)
    expect(state.doc.links).toEqual([])
  })

  it("ignore un widget inconnu", () => {
    const before = JSON.stringify(state.doc)
    state.setProperty('absent', 'label', 'x')
    state.setGeometry('absent', { x: 1 })
    state.setRules('absent', [])
    expect(JSON.stringify(state.doc)).toBe(before)
  })
})

describe('annuler / rétablir', () => {
  it('remonte et rejoue les modifications dans l\'ordre', () => {
    state.addWidget('label')
    state.addWidget('gauge')
    state.setName('A')
    state.undo()
    expect(state.doc.name).toBe('Test')
    state.undo()
    expect(ids()).toEqual(['w1'])
    state.redo()
    expect(ids()).toEqual(['w1', 'w2'])
    state.redo()
    expect(state.doc.name).toBe('A')
    expect(state.canRedo).toBe(false)
  })

  it('efface le « rétablir » dès qu\'une nouvelle modification est faite', () => {
    state.addWidget('label')
    state.undo()
    expect(state.canRedo).toBe(true)
    state.addWidget('gauge')
    expect(state.canRedo).toBe(false)
  })

  it('regroupe un geste continu en une seule étape', () => {
    state.addWidget('label', { x: 200, y: 200 })
    const start = pos('w1')
    state.begin()
    for (let i = 0; i < 40; i++) state.moveSelected(1, 0) // glisser : 40 mouvements
    state.end()
    expect(pos('w1')).toEqual([start[0]! + 40, start[1]])
    state.undo() // une seule annulation ramène au départ
    expect(pos('w1')).toEqual(start)
  })

  it('un geste sans effet ne crée pas d\'étape', () => {
    state.addWidget('label')
    const depth = () => (state.canUndo ? 1 : 0)
    state.undo()
    state.redo()
    state.begin()
    state.end()
    expect(depth()).toBe(1)
    state.undo()
    expect(state.canUndo).toBe(false)
  })

  it('fusionne la saisie rapide d\'une même propriété, mais pas après une pause', () => {
    state.addWidget('label')
    for (const text of ['C', 'CT', 'CTA']) {
      clock += 100
      state.setProperty('w1', 'text', text)
    }
    state.undo()
    expect(state.widget('w1')!.text).toBe('Texte') // toute la saisie annulée d'un coup
    state.redo()
    clock += COALESCE_MS + 1
    state.setProperty('w1', 'text', 'CTA 1')
    state.undo()
    expect(state.widget('w1')!.text).toBe('CTA') // après une pause : nouvelle étape
    // Deux propriétés différentes ne fusionnent pas.
    clock += COALESCE_MS + 1
    state.setProperty('w1', 'text', 'X')
    state.setProperty('w1', 'style.color', '#fff')
    state.undo()
    expect(state.widget('w1')!.text).toBe('X')
  })

  it("retire de la sélection un widget qui n'existe plus après annulation", () => {
    state.addWidget('label')
    state.select(['w1'])
    state.undo()
    expect(state.selection.size).toBe(0)
  })

  it("borne l'historique", () => {
    for (let i = 0; i < HISTORY_LIMIT + 30; i++) state.addWidget('label')
    let steps = 0
    while (state.canUndo) {
      state.undo()
      steps++
    }
    expect(steps).toBe(HISTORY_LIMIT)
  })
})

describe('enregistrement et chargement', () => {
  it('suit l\'état modifié, y compris après annulation', () => {
    expect(state.dirty).toBe(false)
    state.addWidget('label')
    expect(state.dirty).toBe(true)
    state.undo()
    expect(state.dirty).toBe(false) // revenu à l'état enregistré
    state.addWidget('label')
    state.markSaved()
    expect(state.dirty).toBe(false)
    state.setName('Autre')
    expect(state.dirty).toBe(true)
  })

  it('charge un document sans en garder d\'historique, et le copie', () => {
    const doc = { ...newDoc('Chargé'), widgets: [{ ...state.addWidget('label') }] }
    const loaded = make()
    loaded.load(doc)
    expect(loaded.canUndo).toBe(false)
    expect(loaded.dirty).toBe(false)
    loaded.setName('Modifié')
    expect(doc.name).toBe('Chargé') // le document d'origine n'est pas touché
  })

  it('prévient les observateurs à chaque changement', () => {
    const listener = vi.fn()
    const off = state.onChange(listener)
    state.addWidget('label')
    state.select(['w1'])
    expect(listener).toHaveBeenCalled()
    listener.mockClear()
    off()
    state.addWidget('label')
    expect(listener).not.toHaveBeenCalled()
  })
})
