import { describe, expect, it } from 'vitest'
import { AlarmListConfigError, editorSchema, MAX_ROWS, normalizeConfig } from './schema'

describe('normalizeConfig', () => {
  it('applique les valeurs par défaut', () => {
    expect(normalizeConfig({})).toEqual({
      path: undefined,
      states: 'open',
      maxRows: 50,
      allowAck: true,
      theme: 'auto',
    })
    expect(normalizeConfig(undefined).maxRows).toBe(50)
  })

  it('nettoie le chemin et lit les options', () => {
    const config = normalizeConfig({ path: '  Site/Bat A  ', states: 'unacked', maxRows: 10, allowAck: false, theme: 'dark' })
    expect(config).toMatchObject({ path: 'Site/Bat A', states: 'unacked', maxRows: 10, allowAck: false, theme: 'dark' })
    expect(normalizeConfig({ path: '   ' }).path).toBeUndefined()
  })

  it('refuse les valeurs invalides', () => {
    expect(() => normalizeConfig({ states: 'toutes' })).toThrow(AlarmListConfigError)
    expect(() => normalizeConfig({ maxRows: 0 })).toThrow(/maxRows/)
    expect(() => normalizeConfig({ maxRows: MAX_ROWS + 1 })).toThrow(/maxRows/)
    expect(() => normalizeConfig({ maxRows: 2.5 })).toThrow(/maxRows/)
    expect(() => normalizeConfig({ maxRows: '10' })).toThrow(/maxRows/)
    expect(() => normalizeConfig({ theme: 'rose' })).toThrow(/thème/)
    expect(() => normalizeConfig({ path: 42 })).toThrow(/path/)
  })
})

describe('editorSchema', () => {
  it('décrit les propriétés éditables et propose des valeurs acceptées', () => {
    expect(editorSchema.map((f) => f.key)).toEqual(['path', 'states', 'maxRows', 'allowAck', 'theme'])
    const states = editorSchema.find((f) => f.key === 'states')
    if (states?.type !== 'select') throw new Error('states doit être une liste')
    for (const option of states.options) {
      expect(() => normalizeConfig({ states: option.value })).not.toThrow()
    }
  })
})
