import { describe, expect, it } from 'vitest'
import {
  alignSeries,
  appendLive,
  emptySeries,
  formatValue,
  historyToSeries,
  lastPoint,
  maxPointsFor,
  trimBefore,
} from './series'

const at = (seconds: number) => new Date(seconds * 1000).toISOString()

describe('historyToSeries', () => {
  it('convertit les instants en secondes et trie', () => {
    const series = historyToSeries([
      { ts: at(200), value: 2 },
      { ts: at(100), value: 1 },
      { ts: at(300), value: null },
    ])
    expect(series.t).toEqual([100, 200, 300])
    expect(series.v).toEqual([1, 2, null])
    expect(series.liveFrom).toBe(3)
  })

  it('ignore les instants invalides et garde la dernière valeur d\'un instant en double', () => {
    const series = historyToSeries([
      { ts: 'pas une date', value: 9 },
      { ts: at(100), value: 1 },
      { ts: at(100), value: 5 },
    ])
    expect(series.t).toEqual([100])
    expect(series.v).toEqual([5])
  })
})

describe('alignSeries', () => {
  it('aligne sur l\'union des instants, les trous valent null', () => {
    const a = { t: [10, 20, 30], v: [1, 2, 3], liveFrom: 3 }
    const b = { t: [20, 40], v: [200, 400], liveFrom: 2 }
    expect(alignSeries([a, b])).toEqual([
      [10, 20, 30, 40],
      [1, 2, 3, null],
      [null, 200, null, 400],
    ])
  })

  it('accepte des séries vides', () => {
    expect(alignSeries([emptySeries(), emptySeries()])).toEqual([[], [], []])
    expect(alignSeries([])).toEqual([[]])
  })

  it('conserve les valeurs nulles de l\'historique (trous de communication)', () => {
    const a = { t: [1, 2, 3], v: [1, null, 3], liveFrom: 3 }
    expect(alignSeries([a])[1]).toEqual([1, null, 3])
  })
})

describe('appendLive', () => {
  const base = () => historyToSeries([{ ts: at(100), value: 1 }, { ts: at(200), value: 2 }])

  it('ajoute une valeur plus récente', () => {
    const s = base()
    appendLive(s, 210, 3, 0)
    expect(s.t).toEqual([100, 200, 210])
  })

  it('remplace un instant identique et ignore un instant plus ancien', () => {
    const s = base()
    appendLive(s, 200, 9, 0)
    appendLive(s, 150, 7, 0)
    expect(s.t).toEqual([100, 200])
    expect(s.v).toEqual([1, 9])
  })

  it('met à jour en place sous l\'écart minimal, sans toucher aux points de l\'historique', () => {
    const s = base()
    appendLive(s, 205, 3, 60) // premier point temps réel : ajouté même s'il est proche
    expect(s.t).toEqual([100, 200, 205])
    appendLive(s, 230, 4, 60) // moins de 60 s après : remplace le point temps réel
    appendLive(s, 240, 5, 60)
    expect(s.t).toEqual([100, 200, 240])
    expect(s.v).toEqual([1, 2, 5])
    appendLive(s, 310, 6, 60) // écart suffisant : nouveau point
    expect(s.t).toEqual([100, 200, 240, 310])
  })

  it('accepte une valeur null (perte de communication)', () => {
    const s = base()
    appendLive(s, 300, null, 0)
    expect(lastPoint(s)).toEqual({ t: 300, v: null })
  })
})

describe('trimBefore', () => {
  it('retire les points antérieurs et ajuste liveFrom', () => {
    const s = { t: [10, 20, 30, 40], v: [1, 2, 3, 4], liveFrom: 3 }
    trimBefore(s, 25)
    expect(s.t).toEqual([30, 40])
    expect(s.liveFrom).toBe(1)
    trimBefore(s, 100)
    expect(s.t).toEqual([])
    expect(s.liveFrom).toBe(0)
  })
})

describe('maxPointsFor', () => {
  it('vise un point tous les 2 pixels, borné', () => {
    expect(maxPointsFor(800)).toBe(400)
    expect(maxPointsFor(50)).toBe(100)
    expect(maxPointsFor(10_000)).toBe(1500)
  })
})

describe('formatValue', () => {
  it('formate à la française avec l\'unité', () => {
    expect(formatValue(21.456, '°C')).toBe('21,46 °C')
    expect(formatValue(123.456)).toBe('123,5')
    expect(formatValue(0)).toBe('0')
  })

  it('affiche un tiret sans valeur', () => {
    expect(formatValue(null, '°C')).toBe('—')
    expect(formatValue(Number.NaN)).toBe('—')
  })
})
