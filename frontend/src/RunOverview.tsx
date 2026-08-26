import { useEffect, useState } from 'react'
import type { Run } from './RunList'
import RunPerformance from './RunPerformance'
import RunMarket from './RunMarket'
import RunLatency from './RunLatency'

type EquityPoint = { ts: number; equity: number }
type TradeRow = { ts: number; symbol: string; side: string; price: number; qty: number; slot: string | null }
type PnLPoint = { ts: number; slot: string; realized: number; unrealized: number }
type WinRatePoint = { ts: number; slot: string; wins: number; losses: number }
type FillsPoint = { ts: number; slot: string; count: number }
type HealthPoint = { ts: number; component: string; ok: boolean; detail: string | null }
type PricePoint = { ts: number; price: number; trade: TradeRow | null }
type LatencySample = { ts: number; mean: number; p99: number; p999: number }

type Overview = {
  run_id: string
  project: string
  status: string
  encrypted_locked: boolean
  // Whether this run is actively background-polled right now, i.e. whether the
  // SSE stream endpoint would accept a subscription - sourced from the backend
  // rather than re-derived here, since which encrypted logs stay separable
  // (rustle vs. TickTrader-para) is a backend/log-format concern, not a UI one.
  live_tracked: boolean
  equity: EquityPoint[]
  trades: TradeRow[]
  pnl: PnLPoint[]
  win_rates: WinRatePoint[]
  fills: FillsPoint[]
  health: HealthPoint[]
  symbol_prices: Record<string, PricePoint[]>
  channel_latency: Record<string, LatencySample[]>
}

type SSEDelta = {
  equity: EquityPoint[]
  trades: TradeRow[]
  pnl: PnLPoint[]
  win_rates: WinRatePoint[]
  fills: FillsPoint[]
  health: HealthPoint[]
  symbol_prices: Record<string, PricePoint[]>
  channel_latency: Record<string, LatencySample[]>
}

type Tab = 'overview' | 'performance' | 'market' | 'latency'

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'performance', label: 'Performance' },
  { key: 'market', label: 'Market' },
  { key: 'latency', label: 'Latency' },
]

const EQUITY_LIMIT = 500
const TRADE_LIMIT = 50
const PRICE_LIMIT = 500

function mergeLatestByKey<T>(current: T[], incoming: T[], keyOf: (item: T) => string): T[] {
  if (incoming.length === 0) return current
  const byKey = new Map(current.map((item) => [keyOf(item), item]))
  for (const item of incoming) byKey.set(keyOf(item), item)
  return [...byKey.values()]
}

function mergeFillCounts(current: FillsPoint[], incoming: FillsPoint[]): FillsPoint[] {
  if (incoming.length === 0) return current
  const bySlot = new Map(current.map((item) => [item.slot, item]))
  for (const item of incoming) {
    const prior = bySlot.get(item.slot)
    bySlot.set(item.slot, { ts: item.ts, slot: item.slot, count: (prior?.count ?? 0) + item.count })
  }
  return [...bySlot.values()]
}

function mergeCapped<T>(current: Record<string, T[]>, incoming: Record<string, T[]>, limit: number): Record<string, T[]> {
  const keys = Object.keys(incoming)
  if (keys.length === 0) return current
  const next = { ...current }
  for (const key of keys) {
    next[key] = [...(next[key] ?? []), ...incoming[key]].slice(-limit)
  }
  return next
}

function mergeUncapped<T>(current: Record<string, T[]>, incoming: Record<string, T[]>): Record<string, T[]> {
  const keys = Object.keys(incoming)
  if (keys.length === 0) return current
  const next = { ...current }
  for (const key of keys) {
    next[key] = [...(next[key] ?? []), ...incoming[key]]
  }
  return next
}

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
  const [tab, setTab] = useState<Tab>('overview')

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

  const isLive = overview?.live_tracked ?? false

  useEffect(() => {
    if (!isLive) return
    const source = new EventSource(`/api/runs/${run.project}/${run.run_id}/stream`)
    source.addEventListener('message', (event) => {
      const delta: SSEDelta = JSON.parse(event.data)
      setOverview((prev) =>
        prev
          ? {
              ...prev,
              equity: [...prev.equity, ...delta.equity].slice(-EQUITY_LIMIT),
              trades: [...prev.trades, ...delta.trades].slice(-TRADE_LIMIT),
              pnl: mergeLatestByKey(prev.pnl, delta.pnl, (p) => p.slot),
              win_rates: mergeLatestByKey(prev.win_rates, delta.win_rates, (w) => w.slot),
              fills: mergeFillCounts(prev.fills, delta.fills),
              health: mergeLatestByKey(prev.health, delta.health, (h) => h.component),
              symbol_prices: mergeCapped(prev.symbol_prices, delta.symbol_prices, PRICE_LIMIT),
              channel_latency: mergeUncapped(prev.channel_latency, delta.channel_latency),
            }
          : prev,
      )
    })
    source.addEventListener('done', () => source.close())
    return () => source.close()
  }, [isLive, run.project, run.run_id])

  const status = overview?.status ?? run.status
  const isBacktest = status === 'backtest'
  const visibleTabs = TABS.filter((t) => t.key !== 'latency' || !isBacktest)

  function renderTab() {
    if (!overview) return <p>Loading…</p>
    if (tab === 'latency') {
      return <RunLatency channelLatency={overview.channel_latency} health={overview.health} />
    }
    if (overview.encrypted_locked) {
      return <p className="overview-locked">🔒 encrypted — no key configured</p>
    }
    if (tab === 'performance') {
      return <RunPerformance pnl={overview.pnl} winRates={overview.win_rates} fills={overview.fills} />
    }
    if (tab === 'market') {
      return <RunMarket symbolPrices={overview.symbol_prices} />
    }
    return (
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
    )
  }

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

      <nav className="run-tabs">
        {visibleTabs.map(({ key, label }) => (
          <button key={key} className={`run-tab ${tab === key ? 'run-tab--active' : ''}`} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </nav>

      {renderTab()}
    </div>
  )
}
