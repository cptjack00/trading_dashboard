import { useEffect, useState } from 'react'
import type { Run } from './RunList'
import { BarChart, LineChart } from './charts'

type EquityPoint = { ts: number; equity: number }
type PnLPoint = { ts: number; slot: string; realized: number; unrealized: number }
type WinRatePoint = { ts: number; slot: string; wins: number; losses: number }
type FillsPoint = { ts: number; slot: string; count: number }

type Overview = {
  equity: EquityPoint[]
  pnl: PnLPoint[]
  win_rates: WinRatePoint[]
  fills: FillsPoint[]
}

const RUN_COLORS = ['#aa3bff', '#16a34a', '#eab308', '#dc2626']

function runKey(run: Run): string {
  return `${run.project}-${run.run_id}`
}

function formatWinRate(wins: number, losses: number): string {
  const total = wins + losses
  return total === 0 ? '—' : `${((wins / total) * 100).toFixed(1)}%`
}

function totals(overview: Overview) {
  const pnl = overview.pnl.reduce((sum, p) => sum + p.realized + p.unrealized, 0)
  const fills = overview.fills.reduce((sum, f) => sum + f.count, 0)
  const wins = overview.win_rates.reduce((sum, w) => sum + w.wins, 0)
  const losses = overview.win_rates.reduce((sum, w) => sum + w.losses, 0)
  return { pnl, fills, winRate: formatWinRate(wins, losses) }
}

// Every slot name this run has PnL or fill data for, sorted for stable ordering
// within the run - not compared by name across runs, only by count (see below).
function slotsOf(overview: Overview): string[] {
  return [...new Set([...overview.pnl, ...overview.fills].map((r) => r.slot))].sort()
}

export default function RunComparison({ runs, onBack }: { runs: Run[]; onBack: () => void }) {
  const [overviews, setOverviews] = useState<(Overview | null)[]>(runs.map(() => null))

  useEffect(() => {
    let cancelled = false
    Promise.all(
      runs.map((run) =>
        fetch(`/api/runs/${run.project}/${run.run_id}/overview`).then((res) => (res.ok ? res.json() : null)),
      ),
    ).then((data) => {
      if (!cancelled) setOverviews(data)
    })
    return () => {
      cancelled = true
    }
  }, [runs])

  const showDelta = runs.length === 2
  const baseline = overviews[0] ? totals(overviews[0]) : null

  const equitySeries = runs.map((run, i) => {
    const points = overviews[i]?.equity ?? []
    return {
      label: `${run.project}/${run.run_id}`,
      color: RUN_COLORS[i % RUN_COLORS.length],
      points: points.map((p, idx) => ({ x: points.length > 1 ? idx / (points.length - 1) : 0, y: p.equity })),
    }
  })

  // Slots are aligned positionally (1st slot vs. 1st slot, ...), never by name -
  // two runs can use unrelated slot identifiers even when they line up in count.
  const allLoaded = overviews.every((o): o is Overview => o !== null)
  const perRunSlots = allLoaded ? overviews.map(slotsOf) : []
  const slotCount = perRunSlots[0]?.length ?? 0
  // Zero shared slots is still "aligned" (equal counts) - BarChart's own empty
  // state handles that case, so the fallback message here is reserved for an
  // actual count mismatch, never fired when counts agree at zero.
  const slotsAlign = allLoaded && perRunSlots.every((s) => s.length === slotCount)
  const groupLabels = slotsAlign ? Array.from({ length: slotCount }, (_, i) => `Slot ${i + 1}`) : []

  function seriesFor(pick: (overview: Overview, slot: string) => number) {
    return runs.map((run, i) => ({
      label: `${run.project}/${run.run_id}`,
      color: RUN_COLORS[i % RUN_COLORS.length],
      values: perRunSlots[i].map((slot) => pick(overviews[i]!, slot)),
    }))
  }

  return (
    <div className="run-comparison">
      <button className="back-button" onClick={onBack}>
        ← Back to runs
      </button>
      <h2>Comparing {runs.length} runs</h2>

      <table className="data-table comparison-table">
        <thead>
          <tr>
            <th>Run</th>
            <th>PnL</th>
            <th>Fills</th>
            <th>Win rate</th>
            {showDelta && <th>Δ PnL</th>}
          </tr>
        </thead>
        <tbody>
          {runs.map((run, i) => {
            const overview = overviews[i]
            const t = overview ? totals(overview) : null
            const delta = showDelta && i === 1 && t && baseline ? t.pnl - baseline.pnl : null
            return (
              <tr key={runKey(run)}>
                <td>
                  {run.project} / {run.run_id}
                </td>
                <td className={t && t.pnl >= 0 ? 'run-pnl--pos' : 'run-pnl--neg'}>{t ? t.pnl.toFixed(2) : '…'}</td>
                <td>{t ? t.fills : '…'}</td>
                <td>{t ? t.winRate : '…'}</td>
                {showDelta && (
                  <td className={delta !== null ? (delta >= 0 ? 'run-pnl--pos' : 'run-pnl--neg') : undefined}>
                    {delta === null ? '—' : `${delta >= 0 ? '+' : ''}${delta.toFixed(2)}`}
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>

      <section>
        <h3>Cumulative PnL by run progress</h3>
        <LineChart series={equitySeries} />
      </section>

      <section>
        <h3>Per-slot PnL &amp; fills</h3>
        {slotsAlign ? (
          <>
            <BarChart
              groups={groupLabels}
              series={seriesFor((o, slot) => {
                const p = o.pnl.find((x) => x.slot === slot)
                return p ? p.realized + p.unrealized : 0
              })}
            />
            <BarChart
              groups={groupLabels}
              series={seriesFor((o, slot) => o.fills.find((x) => x.slot === slot)?.count ?? 0)}
            />
          </>
        ) : (
          <p className="overview-empty">
            {allLoaded ? 'Selected runs have different slot counts — per-slot comparison unavailable.' : 'Loading…'}
          </p>
        )}
      </section>
    </div>
  )
}
