import { describe, expect, it } from 'vitest'
import {
  alignRects,
  boundsOf,
  contains,
  distributeRects,
  intersects,
  MIN_SIZE,
  rectBetween,
  resizeRect,
  snap,
  snapRect,
} from './geometry'

const r = (x: number, y: number, w: number, h: number) => ({ x, y, w, h })

describe('magnétisme', () => {
  it('cale sur la grille', () => {
    expect(snap(14, 10)).toBe(10)
    expect(snap(15, 10)).toBe(20)
    expect(snap(-4, 10)).toBe(-0) // proche de zéro : 0
    expect(snap(14, 0)).toBe(14) // grille désactivée
    expect(snapRect(r(14, 26, 103, 3), 10)).toEqual(r(10, 30, 100, MIN_SIZE)) // taille jamais sous le minimum
  })
})

describe('formes', () => {
  it('calcule la boîte englobante', () => {
    expect(boundsOf([])).toBeNull()
    expect(boundsOf([r(10, 10, 20, 20), r(50, 5, 10, 10)])).toEqual(r(10, 5, 50, 25))
  })

  it("teste l'intersection et l'inclusion", () => {
    expect(intersects(r(0, 0, 10, 10), r(5, 5, 10, 10))).toBe(true)
    expect(intersects(r(0, 0, 10, 10), r(10, 0, 10, 10))).toBe(false) // simple contact : pas d'intersection
    expect(contains(r(0, 0, 10, 10), 10, 10)).toBe(true)
    expect(contains(r(0, 0, 10, 10), 11, 5)).toBe(false)
  })

  it("normalise un rectangle tiré dans n'importe quel sens", () => {
    expect(rectBetween(50, 60, 10, 20)).toEqual(r(10, 20, 40, 40))
  })
})

describe('alignement', () => {
  const items = [r(10, 10, 20, 20), r(50, 30, 40, 10), r(100, 5, 10, 50)]

  it('aligne sur la boîte englobante', () => {
    const box = boundsOf(items)!
    expect(alignRects(items, 'left').map((i) => i.x)).toEqual([10, 10, 10])
    expect(alignRects(items, 'right').map((i) => i.x + i.w)).toEqual([110, 110, 110])
    expect(alignRects(items, 'center').map((i) => i.x + i.w / 2)).toEqual([box.x + box.w / 2, box.x + box.w / 2, box.x + box.w / 2])
    expect(alignRects(items, 'top').map((i) => i.y)).toEqual([5, 5, 5])
    expect(alignRects(items, 'bottom').map((i) => i.y + i.h)).toEqual([55, 55, 55])
    expect(alignRects(items, 'middle').map((i) => i.y + i.h / 2)).toEqual([30, 30, 30])
  })

  it('ne modifie ni les tailles ni la liste d\'origine, et ignore une sélection unique', () => {
    const aligned = alignRects(items, 'left')
    expect(aligned.map((i) => [i.w, i.h])).toEqual(items.map((i) => [i.w, i.h]))
    expect(items[1]!.x).toBe(50)
    expect(alignRects([items[0]!], 'left')).toEqual([items[0]])
  })

  it('répartit à intervalles réguliers entre les extrêmes', () => {
    const spread = distributeRects([r(0, 0, 10, 10), r(15, 0, 10, 10), r(100, 0, 20, 10)], 'horizontal')
    // extrêmes fixes ; l'espace libre (120 - 40 = 80) est partagé en 2 intervalles de 40
    expect(spread.map((i) => i.x)).toEqual([0, 50, 100])
    const vertical = distributeRects([r(0, 0, 10, 10), r(0, 5, 10, 10), r(0, 90, 10, 10)], 'vertical')
    expect(vertical.map((i) => i.y)).toEqual([0, 45, 90])
  })

  it('répartit dans l\'ordre spatial, pas dans l\'ordre de la liste, et exige 3 éléments', () => {
    const shuffled = [r(100, 0, 10, 10), r(0, 0, 10, 10), r(30, 0, 10, 10)]
    expect(distributeRects(shuffled, 'horizontal').map((i) => i.x)).toEqual([100, 0, 50])
    expect(distributeRects([r(0, 0, 10, 10), r(30, 0, 10, 10)], 'horizontal').map((i) => i.x)).toEqual([0, 30])
  })
})

describe('redimensionnement', () => {
  const base = r(100, 100, 200, 100)

  it('déplace le bord tiré et laisse l\'opposé fixe', () => {
    expect(resizeRect(base, 'e', 30, 0)).toEqual(r(100, 100, 230, 100))
    expect(resizeRect(base, 'w', 30, 0)).toEqual(r(130, 100, 170, 100))
    expect(resizeRect(base, 's', 0, -20)).toEqual(r(100, 100, 200, 80))
    expect(resizeRect(base, 'n', 0, -20)).toEqual(r(100, 80, 200, 120))
    expect(resizeRect(base, 'se', 10, 10)).toEqual(r(100, 100, 210, 110))
    expect(resizeRect(base, 'nw', 10, 10)).toEqual(r(110, 110, 190, 90))
  })

  it('cale les bords sur la grille', () => {
    expect(resizeRect(base, 'se', 13, 8, 10)).toEqual(r(100, 100, 210, 110))
    expect(resizeRect(base, 'nw', -14, -6, 10)).toEqual(r(90, 90, 210, 110))
  })

  it('ne descend jamais sous la taille minimale, sans faire bouger le bord opposé', () => {
    expect(resizeRect(base, 'e', -500, 0)).toEqual(r(100, 100, MIN_SIZE, 100))
    expect(resizeRect(base, 'w', 500, 0)).toEqual(r(292, 100, MIN_SIZE, 100)) // bord droit inchangé : 300
    expect(resizeRect(base, 'n', 0, 500)).toEqual(r(100, 192, 200, MIN_SIZE)) // bord bas inchangé : 200
  })
})
