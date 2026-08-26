import { LineChart } from './charts'

type LatencySample = { ts: number; mean: number; p99: number; p999: number }
type HealthPoint = { ts: number; component: string; ok: boolean; detail: string | null }

const METRIC_COLORS: Record<'mean' | 'p99' | 'p999', string> = {
  mean: '#16a34a',
  p99: '#eab308',
  p999: '#dc2626',
}

function HealthRow({ health }: { health: HealthPoint[] }) {
  if (health.length === 0) return null
  return (
    <section>
      <h3>Health</h3>
      <ul className="health-list">
        {health.map((h) => (
          <li key={h.component} className={h.ok ? 'health-ok' : 'health-down'}>
            {h.component}: {h.ok ? 'ok' : h.detail ?? 'down'}
          </li>
        ))}
      </ul>
    </section>
  )
}

export default function RunLatency({
  channelLatency,
  health,
}: {
  channelLatency: Record<string, LatencySample[]>
  health: HealthPoint[]
}) {
  const channels = Object.keys(channelLatency).sort()
  if (channels.length === 0 && health.length === 0) {
    return <p className="overview-empty">No latency data yet.</p>
  }

  return (
    <>
      <HealthRow health={health} />
      {channels.map((channel) => {
        const samples = channelLatency[channel]
        const latest = samples[samples.length - 1]
        return (
          <section key={channel}>
            <h3>{channel}</h3>
            <p className="latency-stats">
              <span style={{ color: METRIC_COLORS.mean }}>mean {latest.mean.toFixed(1)}ms</span>
              {' · '}
              <span style={{ color: METRIC_COLORS.p99 }}>p99 {latest.p99.toFixed(1)}ms</span>
              {' · '}
              <span style={{ color: METRIC_COLORS.p999 }}>p999 {latest.p999.toFixed(1)}ms</span>
            </p>
            <LineChart
              series={[
                { label: 'mean', color: METRIC_COLORS.mean, points: samples.map((s) => ({ x: s.ts, y: s.mean })) },
                { label: 'p99', color: METRIC_COLORS.p99, points: samples.map((s) => ({ x: s.ts, y: s.p99 })) },
                { label: 'p999', color: METRIC_COLORS.p999, points: samples.map((s) => ({ x: s.ts, y: s.p999 })) },
              ]}
            />
          </section>
        )
      })}
    </>
  )
}
