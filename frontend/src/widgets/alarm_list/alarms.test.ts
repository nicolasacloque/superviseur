import { describe, expect, it } from 'vitest'
import type { Alarm } from '../../api/types'
import {
  applyAlarm,
  compareAlarms,
  describeCondition,
  formatSince,
  matches,
  needsAck,
  sortAlarms,
} from './alarms'

let counter = 0
export function alarm(overrides: Partial<Alarm> = {}): Alarm {
  counter += 1
  return {
    id: `a${counter}`,
    rule_id: `r${counter}`,
    rule_name: null,
    kind: 'high',
    severity: 'warning',
    state: 'active_unacked',
    point_id: `p${counter}`,
    point_name: `Point ${counter}`,
    path: `Site/Bat A/Point ${counter}`,
    unit: '°C',
    threshold: 28,
    value: 30.5,
    raised_at: '2026-09-21T10:00:00Z',
    acked_at: null,
    acked_by: null,
    cleared_at: null,
    ...overrides,
  }
}

describe('needsAck / matches', () => {
  it("les alarmes non acquittées, actives ou terminées, attendent l'opérateur", () => {
    expect(needsAck(alarm({ state: 'active_unacked' }))).toBe(true)
    expect(needsAck(alarm({ state: 'cleared_unacked' }))).toBe(true)
    expect(needsAck(alarm({ state: 'active_acked' }))).toBe(false)
    expect(needsAck(alarm({ state: 'normal' }))).toBe(false)
  })

  it('filtre par groupe d\'états et par préfixe de chemin', () => {
    const acked = alarm({ state: 'active_acked' })
    const closed = alarm({ state: 'normal' })
    expect(matches(acked, { states: 'open' })).toBe(true)
    expect(matches(closed, { states: 'open' })).toBe(false)
    expect(matches(closed, { states: 'closed' })).toBe(true)
    expect(matches(acked, { states: 'unacked' })).toBe(false)
    expect(matches(closed, { states: 'all' })).toBe(true)

    const a = alarm({ path: 'Site/Bat A/CTA-1/Temp' })
    expect(matches(a, { states: 'open', path: 'Site/Bat A' })).toBe(true)
    expect(matches(a, { states: 'open', path: 'Site/Bat B' })).toBe(false)
    expect(matches(alarm({ path: null }), { states: 'open', path: 'Site' })).toBe(false)
  })
})

describe('tri', () => {
  it("place d'abord ce qui attend un acquittement, puis la gravité, puis la récence", () => {
    const olderCritical = alarm({ severity: 'critical', raised_at: '2026-09-21T08:00:00Z' })
    const newerWarning = alarm({ severity: 'warning', raised_at: '2026-09-21T11:00:00Z' })
    const newerCritical = alarm({ severity: 'critical', raised_at: '2026-09-21T09:00:00Z' })
    const ackedCritical = alarm({ severity: 'critical', state: 'active_acked', raised_at: '2026-09-21T12:00:00Z' })
    const sorted = sortAlarms([ackedCritical, newerWarning, olderCritical, newerCritical])
    expect(sorted).toEqual([newerCritical, olderCritical, newerWarning, ackedCritical])
  })

  it('ne modifie pas la liste d\'origine et reste déterministe à égalité', () => {
    const a = alarm({ id: 'a' })
    const b = alarm({ id: 'b' })
    const input = [b, a]
    expect(sortAlarms(input).map((x) => x.id)).toEqual(['a', 'b'])
    expect(input.map((x) => x.id)).toEqual(['b', 'a'])
    expect(compareAlarms(a, a)).toBe(0)
  })
})

describe('applyAlarm', () => {
  const filter = { states: 'open' as const }

  it('ajoute une nouvelle alarme et met à jour une alarme connue', () => {
    const first = alarm({ id: 'x', state: 'active_unacked' })
    let list = applyAlarm([], first, filter, 10)
    expect(list).toEqual([first])
    list = applyAlarm(list, { ...first, state: 'active_acked', acked_by: 'olivia' }, filter, 10)
    expect(list).toHaveLength(1)
    expect(list[0]?.acked_by).toBe('olivia')
  })

  it('retire une alarme qui sort du filtre (ex. close alors que la liste montre les ouvertes)', () => {
    const open = alarm({ id: 'x' })
    const list = applyAlarm([open], { ...open, state: 'normal' }, filter, 10)
    expect(list).toEqual([])
  })

  it('ignore une alarme d\'un autre chemin et borne le nombre de lignes', () => {
    const scoped = { states: 'open' as const, path: 'Site/Bat A' }
    expect(applyAlarm([], alarm({ path: 'Site/Bat B/x' }), scoped, 10)).toEqual([])
    const many = Array.from({ length: 5 }, (_, i) => alarm({ raised_at: `2026-09-21T1${i}:00:00Z` }))
    const list = many.reduce<Alarm[]>((acc, a) => applyAlarm(acc, a, filter, 3), [])
    expect(list).toHaveLength(3)
  })
})

describe('libellés', () => {
  it('décrit la condition de chaque type de règle', () => {
    expect(describeCondition(alarm({ kind: 'high', value: 30.5, threshold: 28 }))).toBe('Seuil haut : 30,5 °C > 28 °C')
    expect(describeCondition(alarm({ kind: 'low', value: 3, threshold: 5 }))).toBe('Seuil bas : 3 °C < 5 °C')
    expect(describeCondition(alarm({ kind: 'state', value: 1, unit: null }))).toBe('État anormal (valeur 1)')
    expect(describeCondition(alarm({ kind: 'stale', threshold: 300 }))).toBe('Aucune nouvelle valeur depuis 300 s')
    expect(describeCondition(alarm({ kind: 'comm_lost' }))).toBe('Communication perdue')
    expect(describeCondition(alarm({ kind: 'bacnet_event' }))).toBe('Événement signalé par le contrôleur')
    expect(describeCondition(alarm({ kind: 'autre' }))).toBe('autre')
    expect(describeCondition(alarm({ kind: 'high', value: null }))).toContain('—')
  })

  it("exprime l'ancienneté", () => {
    const now = Date.parse('2026-09-21T12:00:00Z')
    expect(formatSince('2026-09-21T11:59:30Z', now)).toBe("à l'instant")
    expect(formatSince('2026-09-21T11:55:00Z', now)).toBe('il y a 5 min')
    expect(formatSince('2026-09-21T09:00:00Z', now)).toBe('il y a 3 h')
    expect(formatSince('2026-09-19T12:00:00Z', now)).toBe('il y a 2 j')
    expect(formatSince('2026-09-21T13:00:00Z', now)).toBe("à l'instant") // horloge en avance : jamais négatif
    expect(formatSince('pas une date', now)).toBe('—')
  })
})
