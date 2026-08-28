type PnLPoint = { ts: number; slot: string; realized: number; unrealized: number }
type WinRatePoint = { ts: number; slot: string; wins: number; losses: number; open: boolean }
type FillsPoint = { ts: number; slot: string; count: number }

function formatWinRate(wins: number, losses: number): string {
  const total = wins + losses
  return total === 0 ? '—' : `${((wins / total) * 100).toFixed(1)}% (${wins}-${losses})`
}

function OpenPositions({ pnl, winRates }: { pnl: PnLPoint[]; winRates: WinRatePoint[] }) {
  const openSlots = winRates.filter((w) => w.open).map((w) => w.slot)
  if (openSlots.length === 0) return null

  const pnlBySlot = new Map(pnl.map((p) => [p.slot, p]))
  const totalUnrealized = openSlots.reduce((sum, slot) => sum + (pnlBySlot.get(slot)?.unrealized ?? 0), 0)

  return (
    <table className="data-table open-positions">
      <thead>
        <tr>
          <th colSpan={2}>Open positions</th>
        </tr>
        <tr>
          <th>Slot</th>
          <th>Unrealized PnL</th>
        </tr>
      </thead>
      <tbody>
        {openSlots.map((slot) => {
          const unrealized = pnlBySlot.get(slot)?.unrealized ?? 0
          return (
            <tr key={slot}>
              <td>{slot}</td>
              <td className={unrealized >= 0 ? 'run-pnl--pos' : 'run-pnl--neg'}>{unrealized.toFixed(2)}</td>
            </tr>
          )
        })}
      </tbody>
      <tfoot>
        <tr>
          <td>Total</td>
          <td className={totalUnrealized >= 0 ? 'run-pnl--pos' : 'run-pnl--neg'}>{totalUnrealized.toFixed(2)}</td>
        </tr>
      </tfoot>
    </table>
  )
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

  // Closed-trade PnL only - an open position's floating PnL lives in its own
  // space below instead of being folded into the same total (#11).
  const totalRealized = pnl.reduce((sum, p) => sum + p.realized, 0)
  const totalWins = winRates.reduce((sum, w) => sum + w.wins, 0)
  const totalLosses = winRates.reduce((sum, w) => sum + w.losses, 0)
  const totalFills = fills.reduce((sum, f) => sum + f.count, 0)

  return (
    <>
      <table className="data-table">
        <thead>
          <tr>
            <th>Slot</th>
            <th>Win rate</th>
            <th>Realized PnL</th>
            <th>Fills</th>
          </tr>
        </thead>
        <tbody>
          {slots.map((slot) => {
            const p = pnlBySlot.get(slot)
            const w = winRateBySlot.get(slot)
            const f = fillsBySlot.get(slot)
            const realized = p?.realized ?? 0
            return (
              <tr key={slot}>
                <td>{slot}</td>
                <td>{w ? formatWinRate(w.wins, w.losses) : '—'}</td>
                <td className={realized >= 0 ? 'run-pnl--pos' : 'run-pnl--neg'}>{realized.toFixed(2)}</td>
                <td>{f?.count ?? 0}</td>
              </tr>
            )
          })}
        </tbody>
        <tfoot>
          <tr>
            <td>Total</td>
            <td>{formatWinRate(totalWins, totalLosses)}</td>
            <td className={totalRealized >= 0 ? 'run-pnl--pos' : 'run-pnl--neg'}>{totalRealized.toFixed(2)}</td>
            <td>{totalFills}</td>
          </tr>
        </tfoot>
      </table>
      <OpenPositions pnl={pnl} winRates={winRates} />
    </>
  )
}
