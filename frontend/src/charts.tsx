import {
  createChart,
  ColorType,
  CrosshairMode,
  LineType,
  LineSeries,
  AreaSeries,
  HistogramSeries,
  createSeriesMarkers,
} from 'lightweight-charts'
import type { IChartApi, ISeriesApi, SeriesType, UTCTimestamp } from 'lightweight-charts'
import { useEffect, useRef } from 'react'

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
const FONT = "'JetBrains Mono', 'SFMono-Regular', ui-monospace, Menlo, Consolas, monospace"
// timeAxis=false series carry a 0..1 progress fraction, not real time - lightweight-charts
// requires whole-number time values, so scale the fraction up before handing it over.
const PROGRESS_SCALE = 1_000_000

// lightweight-charts labels every tick itself using UTC getters on the `time`
// value, never the viewer's local timezone - a real (correct) UTC epoch
// therefore displays shifted by the browser's UTC offset. Shift the epoch by
// that same offset before handing it over so the library's own UTC rendering
// lands on the right local wall-clock; format calls then read it back with
// `timeZone: 'UTC'` to match. Also rounds to a whole second, since the
// library requires strictly-ascending integer times and a raw fractional
// epoch can round two adjacent points onto the same second.
function toChartTime(epochSeconds: number): number {
  const offsetMinutes = new Date(epochSeconds * 1000).getTimezoneOffset()
  return Math.round(epochSeconds - offsetMinutes * 60)
}

// The chart draws on <canvas>, which never resolves `var(--x)` CSS custom
// properties on its own - resolve to the literal computed color (recursively,
// since e.g. --accent itself is defined as `var(--phosphor)`) before handing
// a color to the chart.
function resolveColor(color: string): string {
  let value = color
  for (let i = 0; i < 5; i++) {
    const match = /^var\((--[\w-]+)\)$/.exec(value)
    if (!match) break
    value = getComputedStyle(document.documentElement).getPropertyValue(match[1]).trim() || value
  }
  return value
}

function baseOptions(
  width: number,
  formatTime: (t: number) => string,
  timeTicksVisible: boolean,
  // Real time axes get the library's own adaptive tick formatting (it thins
  // ticks to fit and shows only as much precision as the zoom level needs) -
  // only non-time axes (progress %, ordinal bar groups) need a custom
  // formatter, since real dates/times crammed through `formatTime` on every
  // tick just overlap each other.
  tickMarkFormatter?: (t: number) => string,
) {
  const textColor = resolveColor('var(--ink-dim)')
  const gridColor = resolveColor('var(--hairline-soft)')
  const borderColor = resolveColor('var(--hairline)')
  return {
    width,
    height: HEIGHT,
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor,
      fontFamily: FONT,
      fontSize: 11,
    },
    grid: { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
    rightPriceScale: { borderColor },
    timeScale: {
      borderColor,
      visible: timeTicksVisible,
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter,
    },
    localization: { timeFormatter: formatTime },
    crosshair: { mode: CrosshairMode.Normal },
  }
}

// Floating value readout that follows the crosshair - lightweight-charts gives
// you the crosshair line and axis labels for free, but a per-series tooltip is
// left to the caller.
function attachTooltip(
  chart: IChartApi,
  container: HTMLElement,
  seriesList: { api: ISeriesApi<SeriesType>; label: string; color: string }[],
  formatTime: (t: number) => string,
) {
  const tooltip = document.createElement('div')
  tooltip.className = 'chart-tooltip'
  container.appendChild(tooltip)

  chart.subscribeCrosshairMove((param) => {
    if (!param.point || param.time === undefined) {
      tooltip.style.display = 'none'
      return
    }
    const rows = seriesList
      .map((s) => {
        const d = param.seriesData.get(s.api) as { value?: number } | undefined
        if (d?.value === undefined) return null
        return `<div><span class="chart-tooltip-swatch" style="background:${s.color}"></span>${s.label}: ${d.value.toFixed(2)}</div>`
      })
      .filter((r): r is string => r !== null)
    if (rows.length === 0) {
      tooltip.style.display = 'none'
      return
    }
    tooltip.innerHTML = `<div class="chart-tooltip-time">${formatTime(param.time as number)}</div>${rows.join('')}`
    tooltip.style.display = 'block'
    const x = Math.min(Math.max(param.point.x + 12, 0), Math.max(0, container.clientWidth - tooltip.offsetWidth - 4))
    tooltip.style.left = `${x}px`
  })

  return () => tooltip.remove()
}

function Legend({ items }: { items: { label: string; color: string }[] }) {
  if (items.length === 0 || items.length > 6) return null
  return (
    <div className="chart-legend">
      {items.map((item, i) => (
        <span className="chart-legend-item" key={`${item.label}-${item.color}-${i}`}>
          <i style={{ background: resolveColor(item.color) }} />
          {item.label}
        </span>
      ))}
    </div>
  )
}

// Shared by LineChart and StepChart - both are N series sharing a value axis,
// differing only in interpolation (straight vs. stepped) and whether trade
// markers get plotted on the first series.
function useLineLikeChart(
  series: ChartSeries[],
  markers: ChartMarker[],
  timeAxis: boolean,
  stepped: boolean,
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const apisRef = useRef<ISeriesApi<'Line' | 'Area'>[]>([])

  const formatTime = timeAxis
    ? (t: number) => new Date(t * 1000).toLocaleString(undefined, { timeZone: 'UTC' })
    : (t: number) => `${Math.round((t / PROGRESS_SCALE) * 100)}%`
  const progressTickFormatter = (t: number) => `${Math.round((t / PROGRESS_SCALE) * 100)}%`

  const shapeKey = series.map((s) => `${s.label}|${s.color}|${s.fill ? 1 : 0}`).join(',')

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const chart = createChart(
      el,
      baseOptions(el.clientWidth || 600, formatTime, true, timeAxis ? undefined : progressTickFormatter),
    )
    chartRef.current = chart

    apisRef.current = series.map((s) => {
      const color = resolveColor(s.color)
      if (s.fill) {
        return chart.addSeries(AreaSeries, {
          lineColor: color,
          lineWidth: 2,
          topColor: `${color}33`,
          bottomColor: `${color}03`,
          priceLineVisible: false,
          lastValueVisible: false,
        })
      }
      return chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        lineType: stepped ? LineType.WithSteps : LineType.Simple,
        priceLineVisible: false,
        lastValueVisible: false,
      })
    })

    const detachTooltip = attachTooltip(
      chart,
      el,
      series.map((s, i) => ({ api: apisRef.current[i], label: s.label, color: resolveColor(s.color) })),
      formatTime,
    )

    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width
      if (width && width > 0) chart.applyOptions({ width })
    })
    observer.observe(el)

    return () => {
      detachTooltip()
      observer.disconnect()
      chart.remove()
      chartRef.current = null
      apisRef.current = []
    }
    // Rebuilt when the set of series (or its shape) changes; live point updates
    // flow through setData below instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shapeKey, timeAxis, stepped])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const toTime = (x: number) => (timeAxis ? toChartTime(x) : Math.round(x * PROGRESS_SCALE)) as UTCTimestamp
    // Two points rounding onto the same second (fast successive fills) would
    // otherwise violate the library's strictly-ascending requirement and
    // throw, aborting setData for every series still to come - keep the last
    // (most current) value for a colliding second and drop the rest.
    const dedupeAscending = <T extends { time: UTCTimestamp }>(items: T[]): T[] =>
      items.filter((item, i) => i === items.length - 1 || item.time !== items[i + 1].time)

    series.forEach((s, i) => {
      const points = [...s.points].sort((a, b) => a.x - b.x).map((p) => ({ time: toTime(p.x), value: p.y }))
      apisRef.current[i]?.setData(dedupeAscending(points))
    })

    if (apisRef.current[0]) {
      const markerPoints = [...markers]
        .sort((a, b) => a.x - b.x)
        .map((m) => ({
          time: toTime(m.x),
          position: 'inBar' as const,
          color: resolveColor(m.color),
          shape: 'circle' as const,
        }))
      createSeriesMarkers(apisRef.current[0], dedupeAscending(markerPoints))
    }

    chart.timeScale().fitContent()
  }, [series, markers, timeAxis])

  return containerRef
}

export function LineChart({
  series,
  markers,
  timeAxis = true,
}: {
  series: ChartSeries[]
  markers?: ChartMarker[]
  // Whether x is real epoch seconds (ticks/tooltip format as times) or an
  // arbitrary 0..1 progress fraction - RunComparison's cumulative-PnL-by-progress
  // chart opts out of real time formatting.
  timeAxis?: boolean
}) {
  const resolvedMarkers = markers ?? []
  const containerRef = useLineLikeChart(series, resolvedMarkers, timeAxis, false)
  const markerColors = [...new Set(resolvedMarkers.map((m) => m.color))]
  const empty = series.every((s) => s.points.length < 2)

  return (
    <div className="chart-shell">
      <div ref={containerRef} className="chart-canvas" />
      <Legend
        items={[...series.map((s) => ({ label: s.label, color: s.color })), ...markerColors.map((c) => ({ label: 'trade', color: c }))]}
      />
      {empty && <p className="overview-empty overview-empty--overlay">Not enough data yet.</p>}
    </div>
  )
}

// A single cumulative series per label, drawn as a step line (each fill event
// bumps the line immediately rather than interpolating between counts).
export function StepChart({ series }: { series: ChartSeries[] }) {
  const containerRef = useLineLikeChart(series, [], true, true)
  const empty = series.every((s) => s.points.length < 2)

  return (
    <div className="chart-shell">
      <div ref={containerRef} className="chart-canvas" />
      <Legend items={series.map((s) => ({ label: s.label, color: s.color }))} />
      {empty && <p className="overview-empty overview-empty--overlay">Not enough data yet.</p>}
    </div>
  )
}

// Grouped bars, one group per label in `groups`, one bar per series within a
// group - each series' `values` line up positionally with `groups`. Plotted on
// a fake ordinal time axis (cluster of N bar slots + 1 gap slot per group)
// since lightweight-charts has no native categorical axis; group labels are
// rendered as their own row rather than relying on auto-placed time ticks.
export function BarChart({ groups, series }: { groups: string[]; series: BarSeries[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const labelRefs = useRef<(HTMLSpanElement | null)[]>([])

  const n = series.length
  const cluster = n + 1
  const formatTime = (t: number) => groups[Math.floor(t / cluster)] ?? ''
  const shapeKey = series.map((s) => `${s.label}|${s.color}`).join(',') + `::${groups.join(',')}`

  useEffect(() => {
    const el = containerRef.current
    if (!el || groups.length === 0 || n === 0) return
    const chart = createChart(el, {
      ...baseOptions(el.clientWidth || 600, formatTime, false),
      // Categorical, not a real time series - panning/zooming a fixed set of
      // bar groups has no meaning, and would drift the label row below out of
      // sync with the bars (positioned by reading the chart's own coordinate
      // mapping, not CSS).
      handleScroll: false,
      handleScale: false,
    })
    chartRef.current = chart

    const apis = series.map((s) =>
      chart.addSeries(HistogramSeries, { color: resolveColor(s.color), priceLineVisible: false, lastValueVisible: false }),
    )
    apis.forEach((api, i) => {
      api.setData(groups.map((_, g) => ({ time: (g * cluster + i) as UTCTimestamp, value: series[i]!.values[g] ?? 0 })))
    })

    const detachTooltip = attachTooltip(
      chart,
      el,
      series.map((s, i) => ({ api: apis[i]!, label: s.label, color: resolveColor(s.color) })),
      formatTime,
    )

    function positionLabels() {
      // timeToCoordinate only resolves times that are actual bar positions
      // (it maps the data-point index, not an arbitrary continuous time), so
      // center each label between its cluster's first and last real bar
      // rather than asking for the (usually data-less) midpoint directly.
      const scale = chart.timeScale()
      groups.forEach((_, g) => {
        const left = scale.timeToCoordinate((g * cluster) as UTCTimestamp)
        const right = scale.timeToCoordinate((g * cluster + (n - 1)) as UTCTimestamp)
        const labelEl = labelRefs.current[g]
        if (labelEl && left !== null && right !== null) labelEl.style.left = `${(left + right) / 2}px`
      })
    }

    chart.timeScale().fitContent()
    positionLabels()

    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width
      if (width && width > 0) {
        chart.applyOptions({ width })
        positionLabels()
      }
    })
    observer.observe(el)

    return () => {
      detachTooltip()
      observer.disconnect()
      chart.remove()
      chartRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shapeKey, JSON.stringify(series.map((s) => s.values))])

  return (
    <div className="chart-shell">
      <div ref={containerRef} className="chart-canvas" />
      <div className="bar-chart-labels">
        {groups.map((g, i) => (
          <span
            key={g}
            ref={(labelEl) => {
              labelRefs.current[i] = labelEl
            }}
          >
            {g}
          </span>
        ))}
      </div>
      <Legend items={series.map((s) => ({ label: s.label, color: s.color }))} />
      {(groups.length === 0 || series.length === 0) && (
        <p className="overview-empty overview-empty--overlay">Not enough data yet.</p>
      )}
    </div>
  )
}
