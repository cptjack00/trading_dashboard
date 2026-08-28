type PnLPoint = { ts: number; slot: string; realized: number; unrealized: number }
type WinRatePoint = { ts: number; slot: string; wins: number; losses: number; open: boolean }
type FillsPoint = { ts: number; slot: string; count: number }

function formatWinRate(wins: number, losses: number): string {
  const total = wins + losses
  return total === 0 ? '—' : `${((wins / total) * 100).toFixed(1)}% (${wins}-${losses})`
}

export default function RunPerformance({
  pnl,
  winRates,
  fills,
}: {
  pnl: PnLPoint[]
  winRates: WinRatePoint[]
  fills: FillsPoint[]
}) {
  const slots = [...new Set([...pnl, ...winRates, ...fills].map((r) => r.slot))].sort()
  if (slots.length === 0) {
    return <p className="overview-empty">No performance data yet.</p>
  }

  const pnlBySlot = new Map(pnl.map((p) => [p.slot, p]))
  const winRateBySlot = new Map(winRates.map((w) => [w.slot, w]))
  const fillsBySlot = new Map(fills.map((f) => [f.slot, f]))

  const totalPnl = pnl.reduce((sum, p) => sum + p.realized + p.unrealized, 0)
  const totalWins = winRates.reduce((sum, w) => sum + w.wins, 0)
  const totalLosses = winRates.reduce((sum, w) => sum + w.losses, 0)
  const totalFills = fills.reduce((sum, f) => sum + f.count, 0)
  const anyOpen = winRates.some((w) => w.open)

  return (
    <>
      <table className="data-table">
        <thead>
          <tr>
            <th>Slot</th>
            <th>Win rate</th>
            <th>PnL</th>
            <th>Fills</th>
          </tr>
        </thead>
        <tbody>
          {slots.map((slot) => {
            const p = pnlBySlot.get(slot)
            const w = winRateBySlot.get(slot)
            const f = fillsBySlot.get(slot)
            const slotPnl = (p?.realized ?? 0) + (p?.unrealized ?? 0)
            return (
              <tr key={slot}>
                <td>{slot}</td>
                <td>{w ? formatWinRate(w.wins, w.losses) : '—'}</td>
                <td className={slotPnl >= 0 ? 'run-pnl--pos' : 'run-pnl--neg'}>
                  {slotPnl.toFixed(2)}
                  {w?.open && (
                    <sup title="Position still open - its PnL isn't counted as a win or loss yet">*</sup>
                  )}
                </td>
                <td>{f?.count ?? 0}</td>
              </tr>
            )
          })}
        </tbody>
        <tfoot>
          <tr>
            <td>Total</td>
            <td>{formatWinRate(totalWins, totalLosses)}</td>
            <td className={totalPnl >= 0 ? 'run-pnl--pos' : 'run-pnl--neg'}>{totalPnl.toFixed(2)}</td>
            <td>{totalFills}</td>
          </tr>
        </tfoot>
      </table>
      {anyOpen && (
        <p className="latency-stats">
          * still in an open position — its PnL is already counted above but won't be scored as a win or loss
          until it closes flat.
        </p>
      )}
    </>
  )
}
