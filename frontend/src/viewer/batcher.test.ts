import { beforeEach, describe, expect, it } from 'vitest'
import type { LiveSample } from '../api/types'
import { MIN_RENDER_INTERVAL_MS, UpdateBatcher } from './batcher'

let clock: number
let tasks: { fn: () => void; delay: number; cancelled: boolean; done: boolean }[]
let delivered: [string, LiveSample][]

const sample = (point: string, value: number): LiveSample => ({ point, ts: 0, value, status: 'ok' })
const pending = () => tasks.filter((t) => !t.cancelled && !t.done)

function make(): UpdateBatcher {
  return new UpdateBatcher((id, s) => delivered.push([id, s]), {
    now: () => clock,
    schedule: (fn, delay) => {
      const task = { fn: () => { task.done = true; fn() }, delay, cancelled: false, done: false }
      tasks.push(task)
      return () => { task.cancelled = true }
    },
  })
}

beforeEach(() => {
  clock = 10_000
  tasks = []
  delivered = []
})

describe('UpdateBatcher', () => {
  it('ne livre que la dernière valeur de chaque point', () => {
    const batcher = make()
    for (let i = 1; i <= 50; i++) batcher.push('w1', sample('p', i))
    expect(pending()).toHaveLength(1) // un seul rendu planifié pour 50 valeurs
    pending()[0]?.fn()
    expect(delivered).toEqual([['w1', sample('p', 50)]])
  })

  it('espère 100 ms entre deux rendus du même widget (10 par seconde au plus)', () => {
    const batcher = make()
    batcher.push('w1', sample('p', 1))
    pending()[0]?.fn()
    clock += 30
    batcher.push('w1', sample('p', 2))
    expect(pending()[0]?.delay).toBe(MIN_RENDER_INTERVAL_MS - 30)
    clock += MIN_RENDER_INTERVAL_MS - 30
    pending()[0]?.fn()
    expect(delivered.map(([, s]) => s.value)).toEqual([1, 2])
    // Sur 1 seconde de valeurs à 100 Hz, jamais plus de 10 livraisons.
    delivered = []
    for (let step = 0; step < 100; step++) {
      clock += 10
      batcher.push('w1', sample('p', step))
      pending().forEach((t) => t.delay <= 10 && t.fn())
      const due = pending()[0]
      if (due && due.delay === 0) due.fn()
    }
    expect(delivered.length).toBeLessThanOrEqual(11)
  })

  it('traite chaque widget indépendamment', () => {
    const batcher = make()
    batcher.push('w1', sample('p', 1))
    pending()[0]?.fn()
    batcher.push('w2', sample('q', 5)) // w2 n'a encore jamais été rendu : pas d'attente
    expect(pending()[0]?.delay).toBe(0)
    pending()[0]?.fn()
    expect(delivered.map(([id]) => id)).toEqual(['w1', 'w2'])
  })

  it('garde ensemble les valeurs de plusieurs points d\'un même widget', () => {
    const batcher = make()
    batcher.push('w1', sample('a', 1))
    batcher.push('w1', sample('b', 2))
    batcher.push('w1', sample('a', 3))
    pending()[0]?.fn()
    expect(delivered.map(([, s]) => [s.point, s.value])).toEqual([['a', 3], ['b', 2]])
  })

  it('cesse tout après destroy', () => {
    const batcher = make()
    batcher.push('w1', sample('p', 1))
    batcher.destroy()
    expect(tasks.every((t) => t.cancelled)).toBe(true)
    batcher.push('w1', sample('p', 2))
    expect(pending()).toHaveLength(0)
    expect(delivered).toEqual([])
  })
})
