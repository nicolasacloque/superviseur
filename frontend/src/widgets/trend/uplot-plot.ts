import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { PlotAdapter, PlotFactory, PlotSpec } from './plot'
import { formatValue } from './series'

/** Moteur de tracé réel (canvas) : lignes de 2 px, grille discrète, curseur avec repères de 8 px. */
export const uplotFactory: PlotFactory = (host, spec) => {
  let cursorListener: ((index: number | null, left: number, top: number) => void) | null = null
  const { theme } = spec
  const axis = (extra: Partial<uPlot.Axis> = {}): uPlot.Axis => ({
    stroke: theme.muted,
    font: '12px system-ui, -apple-system, "Segoe UI", sans-serif',
    grid: { stroke: theme.grid, width: 1 },
    ticks: { stroke: theme.axis, width: 1 },
    ...extra,
  })
  const single = spec.series.length === 1

  const options: uPlot.Options = {
    width: spec.width,
    height: spec.height,
    legend: { show: false },
    padding: [12, 28, 0, 0], // de l'air à droite : le dernier repère de l'axe ne doit pas être coupé
    cursor: {
      drag: { x: false, y: false },
      x: true,
      y: false,
      points: {
        size: 8,
        width: 2,
        stroke: () => theme.surface, // anneau de 2 px dans la couleur de la surface
        fill: (_u: uPlot, seriesIndex: number) => spec.series[seriesIndex - 1]?.color ?? theme.ink,
      },
    },
    scales: {
      x: { time: true, range: () => spec.xRange },
      y: { range: yRange(spec) },
    },
    axes: [axis({ values: timeLabels }), axis({ size: 56, values: (_u, splits) => splits.map((v) => formatValue(v)) })],
    series: [
      {},
      ...spec.series.map(
        (s): uPlot.Series => ({
          label: s.label,
          stroke: s.color,
          width: 2,
          cap: 'round',
          spanGaps: true,
          points: { show: false },
          fill: single ? `${s.color}1a` : undefined, // ~10 % : un voile, jamais un aplat
        }),
      ),
    ],
    hooks: {
      setCursor: [
        (u) => cursorListener?.(u.cursor.idx ?? null, u.cursor.left ?? -1, u.cursor.top ?? -1),
      ],
    },
  }

  const plot = new uPlot(options, spec.data as uPlot.AlignedData, host)
  const adapter: PlotAdapter = {
    setData: (data) => plot.setData(data as uPlot.AlignedData, true),
    setXRange: (min, max) => {
      spec.xRange = [min, max]
      plot.setScale('x', { min, max })
    },
    setSize: (width, height) => plot.setSize({ width, height }),
    onCursor: (listener) => {
      cursorListener = listener
    },
    destroy: () => plot.destroy(),
  }
  return adapter
}

/** Repères de l'axe du temps en 24 h à la française ; la date seule dès que le pas dépasse un jour. */
function timeLabels(_u: uPlot, splits: number[], _axis: number, _space: number, increment: number): string[] {
  return splits.map((seconds) => {
    const date = new Date(seconds * 1000)
    return increment >= 86400
      ? date.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })
      : date.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
  })
}

function yRange(spec: PlotSpec): uPlot.Scale.Range {
  const { yMin, yMax } = spec
  if (yMin === undefined && yMax === undefined) return (_u, min, max) => padded(min, max)
  return (_u, min, max) => {
    const [low, high] = padded(min, max)
    return [yMin ?? low, yMax ?? high]
  }
}

/** 8 % d'air autour des données ; une courbe plate reste lisible. */
function padded(min: number, max: number): [number, number] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1]
  if (min === max) return [min - 1, max + 1]
  const margin = (max - min) * 0.08
  return [min - margin, max + margin]
}
