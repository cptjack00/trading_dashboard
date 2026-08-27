import { useEffect, useState } from 'react'
import type { Run } from './RunList'
import RunPerformance from './RunPerformance'
import RunMarket from './RunMarket'
import RunLatency from './RunLatency'
import { LineChart } from './charts'

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
  fill_history: Record<string, FillsPoint[]>
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
  fill_history: Record<string, FillsPoint[]>
}

type Tab = 'overview' | 'performance' | 'market' | 'latency'

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'performance', label: 'Performance' },
  { key: 'market', label: 'Market' },
  { key: 'latency', label: 'Latency' },
]

const EQUITY_BUCKET_SECONDS = 1 // matches live.py's EQUITY_BUCKET_SECONDS
const TRADE_LIMIT = 50
const PRICE_LIMIT = 500
const FILLS_HISTORY_LIMIT = 500 // matches live.py's FILLS_HISTORY_LIMIT

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

// Mirrors live.py's `_bucket_equity`: keep the latest point per one-second
// bucket instead of a fixed-count slice, so a live-streaming run's equity
// curve keeps spanning its whole lifetime instead of collapsing back down to
// a recent window the moment any SSE delta arrives.
function bucketEquity(points: EquityPoint[]): EquityPoint[] {
  const byBucket = new Map<number, EquityPoint>()
  for (const p of points) byBucket.set(Math.floor(p.ts / EQUITY_BUCKET_SECONDS), p)
  return [...byBucket.entries()].sort((a, b) => a[0] - b[0]).map(([, p]) => p)
}

function EquityCurve({ points }: { points: EquityPoint[] }) {
  return (
    <LineChart
      series={[{ label: 'Equity', color: 'var(--accent)', fill: true, points: points.map((p) => ({ x: p.ts, y: p.equity })) }]}
    />
  )
}

function StopButton({ project, runId }: { project: string; runId: string }) {
  const [confirming, setConfirming] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [stopped, setStopped] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleConfirm() {
    setStopping(true)
    setError(null)
    const res = await fetch(`/api/runs/${project}/${runId}/stop`, { method: 'POST' })
    setStopping(false)
    if (res.ok) {
      setStopped(true)
    } else {
      const body = await res.json().catch(() => null)
      setError(body?.detail ?? 'Could not stop run')
    }
  }

  if (stopped) {
    return <span className="stop-confirm">Stop requested.</span>
  }

  if (!confirming) {
    return (
      <button className="action-btn stop" onClick={() => setConfirming(true)}>
        Stop run
      </button>
    )
  }

  return (
    <span className="stop-confirm">
      Stop this run?
      <button className="action-btn confirm" disabled={stopping} onClick={handleConfirm}>
        Confirm stop
      </button>
      <button className="action-cancel" disabled={stopping} onClick={() => setConfirming(false)}>
        Cancel
      </button>
      {error && <span role="alert">{error}</span>}
    </span>
  )
}

export default function RunOverview({ run }: { run: Run }) {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [tab, setTab] = useState<Tab>('overview')

  useEffect(() => {
    let cancelled = false
    // App keys RunOverview by run identity, so a run switch remounts this
    // component fresh (new state, new chart instances) instead of patching in
    // place - no reset-on-change needed here.
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
              equity: bucketEquity([...prev.equity, ...delta.equity]),
              trades: [...prev.trades, ...delta.trades].slice(-TRADE_LIMIT),
              pnl: mergeLatestByKey(prev.pnl, delta.pnl, (p) => p.slot),
              win_rates: mergeLatestByKey(prev.win_rates, delta.win_rates, (w) => w.slot),
              fills: mergeFillCounts(prev.fills, delta.fills),
              health: mergeLatestByKey(prev.health, delta.health, (h) => h.component),
              symbol_prices: mergeCapped(prev.symbol_prices, delta.symbol_prices, PRICE_LIMIT),
              channel_latency: mergeUncapped(prev.channel_latency, delta.channel_latency),
              fill_history: mergeCapped(prev.fill_history, delta.fill_history, FILLS_HISTORY_LIMIT),
            }
          : prev,
      )
    })
    source.addEventListener('done', () => source.close())
    // EventSource auto-retries by default and can't distinguish a 404 (the
    // run ended right as the connection opened - a real race, since /overview
    // and /stream are two separate requests) from a transient network blip.
    // Close outright rather than let it silently retry forever.
    source.onerror = () => source.close()
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
      return (
        <RunPerformance
          pnl={overview.pnl}
          winRates={overview.win_rates}
          fills={overview.fills}
          fillHistory={overview.fill_history}
        />
      )
    }
    if (tab === 'market') {
      return <RunMarket symbolPrices={overview.symbol_prices} />
    }
    return (
      <div className="panel">
        <div className="panel-head">
          <span className="eyebrow">Equity</span>
        </div>
        <div className="chart-pad">
          <EquityCurve points={overview.equity} />
        </div>
      </div>
    )
  }

  return (
    <div className="run-overview">
      <div className="stage-head">
        <div className="stage-title-row">
          <span className={`pulse pulse--${status === 'live' ? 'live' : 'dead'}`} aria-hidden="true" />
          <span className="stage-title">{run.run_id}</span>
          <span className={`tag ${run.project}`}>{run.project}</span>
          <span className={`badge ${status}`}>{status.toUpperCase()}</span>
        </div>
        {status === 'live' && (
          <div className="action-actions">
            <StopButton project={run.project} runId={run.run_id} />
          </div>
        )}
      </div>

      <nav className="tabbar">
        {visibleTabs.map(({ key, label }) => (
          <button key={key} className={`tabbtn${tab === key ? ' active' : ''}`} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </nav>

      <div className="tab-body">{renderTab()}</div>
    </div>
  )
}
