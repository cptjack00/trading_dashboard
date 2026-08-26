export type ChartSeries = { label: string; color: string; points: { x: number; y: number }[] }
export type ChartMarker = { x: number; y: number; color: string }
export type BarSeries = { label: string; color: string; values: number[] }

const WIDTH = 600
const HEIGHT = 160

export function LineChart({ series, markers }: { series: ChartSeries[]; markers?: ChartMarker[] }) {
  const allPoints = series.flatMap((s) => s.points)
  if (allPoints.length < 2) {
    return <p className="overview-empty">Not enough data yet.</p>
  }

  const xs = allPoints.map((p) => p.x)
  const ys = allPoints.map((p) => p.y)
  const minX = Math.min(...xs)
  const spanX = Math.max(...xs) - minX || 1
  const minY = Math.min(...ys)
  const spanY = Math.max(...ys) - minY || 1
  const sx = (x: number) => ((x - minX) / spanX) * WIDTH
  const sy = (y: number) => HEIGHT - ((y - minY) / spanY) * HEIGHT

  return (
    <svg className="line-chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} preserveAspectRatio="none">
      {series.map((s) => (
        <path
          key={s.label}
          d={s.points
            .map((p, i) => `${i === 0 ? 'M' : 'L'} ${sx(p.x).toFixed(2)} ${sy(p.y).toFixed(2)}`)
            .join(' ')}
          fill="none"
          stroke={s.color}
          strokeWidth={2}
        />
      ))}
      {markers?.map((m, i) => (
        <circle key={i} cx={sx(m.x)} cy={sy(m.y)} r={4} fill={m.color} />
      ))}
    </svg>
  )
}

// Grouped bars, one group per label in `groups`, one bar per series within a group.
// Values may be negative (drawn below the zero line) - each series' `values` must
// line up positionally with `groups`.
export function BarChart({ groups, series }: { groups: string[]; series: BarSeries[] }) {
  if (groups.length === 0 || series.length === 0) {
    return <p className="overview-empty">Not enough data yet.</p>
  }

  const maxAbs = Math.max(1, ...series.flatMap((s) => s.values.map((v) => Math.abs(v))))
  const zeroY = HEIGHT / 2
  const groupWidth = WIDTH / groups.length
  const gap = 4
  const barWidth = Math.max((groupWidth - gap * (series.length + 1)) / series.length, 0)

  return (
    <svg className="bar-chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} preserveAspectRatio="none">
      <line x1={0} y1={zeroY} x2={WIDTH} y2={zeroY} stroke="var(--border)" strokeWidth={1} />
      {groups.map((group, gi) =>
        series.map((s, si) => {
          const value = s.values[gi] ?? 0
          const barHeight = (Math.abs(value) / maxAbs) * zeroY
          const x = gi * groupWidth + gap + si * (barWidth + gap)
          const y = value >= 0 ? zeroY - barHeight : zeroY
          return <rect key={`${group}-${s.label}`} x={x} y={y} width={barWidth} height={barHeight} fill={s.color} />
        }),
      )}
    </svg>
  )
}
