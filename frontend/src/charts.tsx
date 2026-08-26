export type ChartSeries = { label: string; color: string; points: { x: number; y: number }[] }
export type ChartMarker = { x: number; y: number; color: string }

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
