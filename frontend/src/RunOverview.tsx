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

const EQUITY_LIMIT = 500
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

const TRADES_PAGE_SIZE = 100
const SCROLL_LOAD_THRESHOLD_PX = 40

// `trades` is the live-updating recent tail (from /overview + SSE, capped at
// TRADE_LIMIT); `older` holds pages fetched on demand as the operator scrolls
// past what's already loaded, via GET .../trades?before=<ts>. Both stay
// chronological (ascending ts) so `[...older, ...trades]` is one ordered list.
function TradeTape({ project, runId, trades }: { project: string; runId: string; trades: TradeRow[] }) {
  const [older, setOlder] = useState<TradeRow[]>([])
  const [hasMore, setHasMore] = useState(true)
  const [loading, setLoading] = useState(false)

  // Reset the loaded-older-pages state when the run identity changes, without
  // an effect: https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  const [trackedRun, setTrackedRun] = useState(`${project}/${runId}`)
  const runIdentity = `${project}/${runId}`
  if (runIdentity !== trackedRun) {
    setTrackedRun(runIdentity)
    setOlder([])
    setHasMore(true)
  }

  const all = [...older, ...trades]

  async function loadMore() {
    if (loading || !hasMore || all.length === 0) return
    setLoading(true)
    const oldestTs = all[0].ts
    const res = await fetch(
      `/api/runs/${project}/${runId}/trades?before=${oldestTs}&limit=${TRADES_PAGE_SIZE}`,
    )
    setLoading(false)
    if (!res.ok) return
    const page: TradeRow[] = await res.json()
    if (page.length === 0) {
      setHasMore(false)
      return
    }
    setOlder((prev) => [...page, ...prev])
  }

  function handleScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget
    if (el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_LOAD_THRESHOLD_PX) {
      void loadMore()
    }
  }

  if (all.length === 0) {
    return <p className="overview-empty">No trades yet.</p>
  }
  const recent = [...all].reverse()
  return (
    <div className="trade-tape-scroll" onScroll={handleScroll}>
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
      {loading && <p className="overview-empty">Loading…</p>}
    </div>
  )
}

export default function RunOverview({ run }: { run: Run }) {
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
              fill_history: mergeCapped(prev.fill_history, delta.fill_history, FILLS_HISTORY_LIMIT),
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
      <>
        <div className="panel">
          <div className="panel-head">
            <span className="eyebrow">Equity</span>
          </div>
          <div className="chart-pad">
            <EquityCurve points={overview.equity} />
          </div>
        </div>
        <div className="panel">
          <div className="panel-head">
            <span className="eyebrow">Trade tape</span>
          </div>
          <div className="tape-wrap">
            <TradeTape project={run.project} runId={run.run_id} trades={overview.trades} />
          </div>
        </div>
      </>
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
