import { LineChart } from './charts'

type TradeRow = { ts: number; symbol: string; side: string; price: number; qty: number; slot: string | null }
type PricePoint = { ts: number; price: number; trade: TradeRow | null }

export default function RunMarket({ symbolPrices }: { symbolPrices: Record<string, PricePoint[]> }) {
  const symbols = Object.keys(symbolPrices).sort()
  if (symbols.length === 0) {
    return <p className="overview-empty">No market data yet.</p>
  }

  return (
    <>
      {symbols.map((symbol) => {
        const points = symbolPrices[symbol]
        const markers = points
          .filter((p) => p.trade)
          .map((p) => ({ x: p.ts, y: p.price, color: p.trade!.side === 'buy' ? '#16a34a' : '#dc2626' }))
        return (
          <section key={symbol}>
            <h3>{symbol}</h3>
            <LineChart
              series={[
                { label: symbol, color: 'var(--accent)', points: points.map((p) => ({ x: p.ts, y: p.price })) },
              ]}
              markers={markers}
            />
          </section>
        )
      })}
    </>
  )
}
