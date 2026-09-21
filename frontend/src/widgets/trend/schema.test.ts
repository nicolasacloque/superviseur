import { describe, expect, it } from 'vitest'
import { editorSchema, MAX_SERIES, normalizeConfig, TrendConfigError } from './schema'

const points = (n: number) => Array.from({ length: n }, (_, i) => ({ point: `p${i}` }))

describe('normalizeConfig', () => {
  it('applique les valeurs par défaut', () => {
    const config = normalizeConfig({ bind: { points: [{ point: 'a', label: 'Soufflage' }] } })
    expect(config).toMatchObject({
      range: '1h',
      live: true,
      rangeSelector: true,
      theme: 'auto',
      bind: { points: [{ point: 'a', label: 'Soufflage' }] },
    })
  })

  it('accepte 8 points au plus', () => {
    expect(normalizeConfig({ bind: { points: points(MAX_SERIES) } }).bind.points).toHaveLength(8)
    expect(() => normalizeConfig({ bind: { points: points(9) } })).toThrow(/8 points au maximum/)
  })

  it('exige au moins un point identifié, sans doublon', () => {
    expect(() => normalizeConfig({})).toThrow(TrendConfigError)
    expect(() => normalizeConfig({ bind: { points: [] } })).toThrow(/au moins un point/)
    expect(() => normalizeConfig({ bind: { points: [{ point: '' }] } })).toThrow(/manquant/)
    expect(() => normalizeConfig({ bind: { points: [{ point: 'a' }, { point: 'a' }] } })).toThrow(/qu'une fois/)
  })

  it('accepte une liste d\'identifiants simples', () => {
    expect(normalizeConfig({ bind: { points: ['a', 'b'] } }).bind.points.map((p) => p.point)).toEqual(['a', 'b'])
  })

  it('valide période, thème et bornes de l\'axe', () => {
    const bind = { points: ['a'] }
    expect(() => normalizeConfig({ bind, range: '2j' })).toThrow(/période inconnue/)
    expect(() => normalizeConfig({ bind, theme: 'rose' })).toThrow(/thème inconnu/)
    expect(() => normalizeConfig({ bind, yMin: 'x' })).toThrow(/yMin doit être un nombre/)
    expect(() => normalizeConfig({ bind, yMin: 10, yMax: 5 })).toThrow(/inférieur/)
    const ok = normalizeConfig({ bind, yMin: 0, yMax: 100, live: false, rangeSelector: false, range: '30d' })
    expect(ok).toMatchObject({ yMin: 0, yMax: 100, live: false, rangeSelector: false, range: '30d' })
  })
})

describe('editorSchema', () => {
  it('décrit les propriétés éditables du widget', () => {
    expect(editorSchema.map((f) => f.key)).toEqual([
      'bind.points',
      'range',
      'live',
      'rangeSelector',
      'yMin',
      'yMax',
      'theme',
    ])
    const points = editorSchema.find((f) => f.key === 'bind.points')
    expect(points).toMatchObject({ type: 'points', min: 1, max: 8 })
  })

  it('propose exactement les périodes que le widget sait afficher', () => {
    const range = editorSchema.find((f) => f.key === 'range')
    if (range?.type !== 'select') throw new Error('range doit être une liste')
    for (const option of range.options) {
      expect(() => normalizeConfig({ bind: { points: ['a'] }, range: option.value })).not.toThrow()
    }
  })
})
