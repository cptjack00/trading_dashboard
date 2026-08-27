import uPlot from 'uplot'
import { useEffect, useRef } from 'react'
import 'uplot/dist/uPlot.min.css'

export type ChartSeries = {
  label: string
  color: string
  points: { x: number; y: number }[]
  // Filled-area treatment (shaded to zero) - the equity curve wants this, other
  // LineChart callers (comparison overlays, latency lines, market price) don't.
  fill?: boolean
}
export type ChartMarker = { x: number; y: number; color: string }
export type BarSeries = { label: string; color: string; values: number[] }

const HEIGHT = 200

// uPlot draws on <canvas>, which never resolves `var(--x)` CSS custom
// properties on its own - resolve to the literal computed color (recursively,
// since e.g. --accent itself is defined as `var(--phosphor)`) before handing
// a color to any uPlot stroke/fill option.
function resolveColor(color: string): string {
  let value = color
  for (let i = 0; i < 5; i++) {
    const match = /^var\((--[\w-]+)\)$/.exec(value)
    if (!match) break
    value = getComputedStyle(document.documentElement).getPropertyValue(match[1]).trim() || value
  }
  return value
}

// Chart instances mount into a ref'd div that's always present in the DOM (an
// empty-state message overlays it instead of replacing it) so the one-time
// creation effect below reliably has a real element to attach to, even when a
// run's data starts empty and arrives moments later via its first fetch/poll.
function useUplot(getOptions: (width: number) => uPlot.Options, data: uPlot.AlignedData) {
  const containerRef = useRef<HTMLDivElement>(null)
  const plotRef = useRef<uPlot | null>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const plot = new uPlot(getOptions(el.clientWidth || 600), data, el)
    plotRef.current = plot

    const xs = data[0] as number[]
    function resetZoom() {
      if (xs.length > 0) plot.setScale('x', { min: xs[0], max: xs[xs.length - 1] })
    }
    el.addEventListener('dblclick', resetZoom)

    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width
      if (width && width > 0) plot.setSize({ width, height: HEIGHT })
    })
    observer.observe(el)

    return () => {
      el.removeEventListener('dblclick', resetZoom)
      observer.disconnect()
      plot.destroy()
      plotRef.current = null
    }
    // Rebuilt once per mount; live updates flow through setData below instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    plotRef.current?.setData(data, false)
  }, [data])

  return containerRef
}

// Drag-to-zoom on the x axis (uPlot's own standard recipe: turn a cursor
// selection into a scale change, then clear the selection). Double-click
// resets, via `resetZoom` in `useUplot`.
const ZOOM_HOOKS: uPlot.Hooks.Arrays = {
  setSelect: [
    (u: uPlot) => {
      if (u.select.width > 4) {
        const min = u.posToVal(u.select.left, 'x')
        const max = u.posToVal(u.select.left + u.select.width, 'x')
        u.setScale('x', { min, max })
        u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false)
      }
    },
  ],
}

function alignToSharedX(series: ChartSeries[], markers: ChartMarker[]) {
  const xsSet = new Set<number>()
  for (const s of series) for (const p of s.points) xsSet.add(p.x)
  for (const m of markers) xsSet.add(m.x)
  const xs = [...xsSet].sort((a, b) => a - b)
  const xIndex = new Map(xs.map((x, i) => [x, i]))

  const seriesYs = series.map((s) => {
    const ys: (number | null)[] = new Array(xs.length).fill(null)
    for (const p of s.points) ys[xIndex.get(p.x)!] = p.y
    return ys
  })

  const markerColors = [...new Set(markers.map((m) => m.color))]
  const markerYs = markerColors.map((color) => {
    const ys: (number | null)[] = new Array(xs.length).fill(null)
    for (const m of markers) if (m.color === color) ys[xIndex.get(m.x)!] = m.y
    return ys
  })

  return { xs, seriesYs, markerColors, markerYs }
}

export function LineChart({
  series,
  markers,
  timeAxis = true,
}: {
  series: ChartSeries[]
  markers?: ChartMarker[]
  // Whether x is real epoch seconds (uPlot formats ticks as times/dates) or
  // an arbitrary numeric axis - RunComparison's cumulative-PnL-by-progress
  // chart uses a normalized 0..1 fraction, not real time, so it opts out.
  timeAxis?: boolean
}) {
  const { xs, seriesYs, markerColors, markerYs } = alignToSharedX(series, markers ?? [])
  const data = [xs, ...seriesYs, ...markerYs] as uPlot.AlignedData

  const containerRef = useUplot(
    (width) => ({
      width,
      height: HEIGHT,
      cursor: { drag: { x: true, y: false } },
      legend: { show: series.length + markerColors.length <= 6 },
      scales: { x: { time: timeAxis } },
      series: [
        {},
        ...series.map((s) => {
          const color = resolveColor(s.color)
          return {
            label: s.label,
            stroke: color,
            width: 2,
            fill: s.fill ? `${color}33` : undefined,
            points: { show: false },
          }
        }),
        ...markerColors.map((rawColor) => {
          const color = resolveColor(rawColor)
          return {
            label: 'trade',
            stroke: color,
            fill: color,
            paths: () => null,
            points: { show: true, size: 6 },
          }
        }),
      ],
      axes: [{}, {}],
      hooks: ZOOM_HOOKS,
    }),
    data,
  )

  return (
    <div className="chart-shell">
      <div ref={containerRef} className="uplot-wrap" />
      {xs.length < 2 && <p className="overview-empty overview-empty--overlay">Not enough data yet.</p>}
    </div>
  )
}

// Grouped bars, one group per label in `groups`, one bar per series within a
// group - each series' `values` line up positionally with `groups`.
export function BarChart({ groups, series }: { groups: string[]; series: BarSeries[] }) {
  const xs = groups.map((_, i) => i)
  const data = [xs, ...series.map((s) => groups.map((_, i) => s.values[i] ?? 0))] as uPlot.AlignedData

  const containerRef = useUplot(
    (width) => ({
      width,
      height: HEIGHT,
      cursor: { drag: { x: false, y: false } },
      scales: { x: { time: false, range: [-0.5, Math.max(0, groups.length - 0.5)] } },
      series: [
        {},
        ...series.map((s) => {
          const color = resolveColor(s.color)
          return {
            label: s.label,
            stroke: color,
            fill: color,
            paths: uPlot.paths.bars!({ size: [0.6, 100], gap: 2 }),
            points: { show: false },
          }
        }),
      ],
      axes: [{ values: (_u, splits) => splits.map((v) => groups[v] ?? '') }, {}],
    }),
    data,
  )

  return (
    <div className="chart-shell">
      <div ref={containerRef} className="uplot-wrap" />
      {(groups.length === 0 || series.length === 0) && (
        <p className="overview-empty overview-empty--overlay">Not enough data yet.</p>
      )}
    </div>
  )
}

// A single cumulative series per label, drawn as a step chart (each fill event
// bumps the line immediately rather than interpolating between counts).
export function StepChart({ series }: { series: ChartSeries[] }) {
  const { xs, seriesYs } = alignToSharedX(series, [])
  const data = [xs, ...seriesYs] as uPlot.AlignedData

  const containerRef = useUplot(
    (width) => ({
      width,
      height: HEIGHT,
      cursor: { drag: { x: true, y: false } },
      legend: { show: series.length <= 6 },
      scales: { x: { time: false } },
      series: [
        {},
        ...series.map((s) => ({
          label: s.label,
          stroke: resolveColor(s.color),
          width: 2,
          paths: uPlot.paths.stepped!({ align: 1 }),
          points: { show: false },
        })),
      ],
      axes: [{}, {}],
      hooks: ZOOM_HOOKS,
    }),
    data,
  )

  return (
    <div className="chart-shell">
      <div ref={containerRef} className="uplot-wrap" />
      {xs.length < 2 && <p className="overview-empty overview-empty--overlay">Not enough data yet.</p>}
    </div>
  )
}
