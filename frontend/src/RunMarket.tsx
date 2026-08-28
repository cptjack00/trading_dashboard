import { LineChart } from './charts'

type PricePoint = { ts: number; price: number }

export default function RunMarket({ symbolPrices }: { symbolPrices: Record<string, PricePoint[]> }) {
  const symbols = Object.keys(symbolPrices).sort()
  if (symbols.length === 0) {
    return <p className="overview-empty">No market data yet.</p>
  }

  return (
    <>
      {symbols.map((symbol) => {
        const points = symbolPrices[symbol]
        return (
          <section key={symbol}>
            <h3>{symbol}</h3>
            <LineChart
              series={[
                { label: symbol, color: 'var(--accent)', points: points.map((p) => ({ x: p.ts, y: p.price })) },
              ]}
            />
          </section>
        )
      })}
    </>
  )
}
