import { useEffect, useState } from 'react'
import type { Run } from './RunList'

type EquityPoint = { ts: number; equity: number }
type TradeRow = { ts: number; symbol: string; side: string; price: number; qty: number; slot: string | null }

type Overview = {
  run_id: string
  project: string
  status: string
  encrypted_locked: boolean
  equity: EquityPoint[]
  trades: TradeRow[]
}

const EQUITY_LIMIT = 500
const TRADE_LIMIT = 50

function EquityCurve({ points }: { points: EquityPoint[] }) {
  if (points.length < 2) {
    return <p className="overview-empty">Not enough data yet.</p>
  }
  const width = 600
  const height = 160
  const values = points.map((p) => p.equity)
  const min = Math.min(...values)
  const span = Math.max(...values) - min || 1
  const step = width / (points.length - 1)
  const path = points
    .map((p, i) => {
      const x = i * step
      const y = height - ((p.equity - min) / span) * height
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
    })
    .join(' ')

  return (
    <svg className="equity-curve" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth={2} />
    </svg>
  )
}

function TradeTape({ trades }: { trades: TradeRow[] }) {
  if (trades.length === 0) {
    return <p className="overview-empty">No trades yet.</p>
  }
  const recent = [...trades].reverse()
  return (
    <table className="trade-tape">
      <thead>
        <tr>
          <th>Time</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Price</th>
        </tr>
      </thead>
      <tbody>
        {recent.map((t, i) => (
          <tr key={i} className={`trade-row trade-row--${t.side}`}>
            <td>{new Date(t.ts * 1000).toLocaleTimeString()}</td>
            <td>{t.side.toUpperCase()}</td>
            <td>{t.qty}</td>
            <td>{t.price}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default function RunOverview({ run, onBack }: { run: Run; onBack: () => void }) {
  const [overview, setOverview] = useState<Overview | null>(null)

  useEffect(() => {
    let cancelled = false
    // Component always remounts fresh on run change (App only ever shows
    // RunOverview after a RunList selection), so no reset-on-change needed.
    fetch(`/api/runs/${run.project}/${run.run_id}/overview`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: Overview | null) => {
        if (!cancelled && data) setOverview(data)
      })
    return () => {
      cancelled = true
    }
  }, [run.project, run.run_id])

  const isLive = overview?.status === 'live' && !overview.encrypted_locked

  useEffect(() => {
    if (!isLive) return
    const source = new EventSource(`/api/runs/${run.project}/${run.run_id}/stream`)
    source.addEventListener('message', (event) => {
      const delta: { equity: EquityPoint[]; trades: TradeRow[] } = JSON.parse(event.data)
      setOverview((prev) =>
        prev
          ? {
              ...prev,
              equity: [...prev.equity, ...delta.equity].slice(-EQUITY_LIMIT),
              trades: [...prev.trades, ...delta.trades].slice(-TRADE_LIMIT),
            }
          : prev,
      )
    })
    source.addEventListener('done', () => source.close())
    return () => source.close()
  }, [isLive, run.project, run.run_id])

  const status = overview?.status ?? run.status

  return (
    <div className="run-overview">
      <button className="back-button" onClick={onBack}>
        ← Back to runs
      </button>
      <header className="run-overview-header">
        <span className={`pulse pulse--${status === 'live' ? 'live' : 'dead'}`} aria-hidden="true" />
        <h2>
          {run.project} / {run.run_id}
        </h2>
        <span className={`run-badge run-badge--${status}`}>{status.toUpperCase()}</span>
      </header>

      {/* ponytail: only the Overview tab exists yet - Performance/Market/Latency land in later issues */}
      <nav className="run-tabs">
        <span className="run-tab run-tab--active">Overview</span>
      </nav>

      {!overview ? (
        <p>Loading…</p>
      ) : overview.encrypted_locked ? (
        <p className="overview-locked">🔒 encrypted — no key configured</p>
      ) : (
        <>
          <section>
            <h3>Equity</h3>
            <EquityCurve points={overview.equity} />
          </section>
          <section>
            <h3>Trade tape</h3>
            <TradeTape trades={overview.trades} />
          </section>
        </>
      )}
    </div>
  )
}
