import { describe, expect, it } from 'vitest'
import { hrefFor, parseRoute } from './router'

describe('routeur', () => {
  it.each([
    ['', { name: 'list' }],
    ['#/', { name: 'list' }],
    ['#/login', { name: 'login' }],
    ['#/view/cta-1', { name: 'view', slug: 'cta-1' }],
    ['#/edit/cta-1', { name: 'edit', slug: 'cta-1' }],
    ['#/edit/new', { name: 'edit', slug: null }],
    ['#/view/', { name: 'list' }],
    ['#/inconnu/x', { name: 'list' }],
    ['#/view/%E0%A4%A', { name: 'list' }], // encodage invalide
  ])('%s', (hash, expected) => {
    expect(parseRoute(hash)).toEqual(expected)
  })

  it('les adresses font l\'aller-retour', () => {
    for (const route of [{ name: 'list' }, { name: 'login' }, { name: 'view', slug: 'a b/é' }, { name: 'edit', slug: null }, { name: 'edit', slug: 'x' }] as const) {
      expect(parseRoute(hrefFor(route))).toEqual(route)
    }
  })
})
