import type { Aligned } from './series'
import type { Theme } from '../theme'

export interface PlotSpec {
  width: number
  height: number
  series: { label: string; color: string }[]
  theme: Theme
  yMin?: number
  yMax?: number
  data: Aligned
  xRange: [number, number]
}

/** Ce dont le widget a besoin d'un moteur de tracé : uPlot en production, une doublure en test. */
export interface PlotAdapter {
  setData(data: Aligned): void
  setXRange(min: number, max: number): void
  setSize(width: number, height: number): void
  /** Position du curseur : index de la donnée la plus proche, et coordonnées dans l'hôte. */
  onCursor(listener: (index: number | null, left: number, top: number) => void): void
  destroy(): void
}

export type PlotFactory = (host: HTMLElement, spec: PlotSpec) => PlotAdapter
