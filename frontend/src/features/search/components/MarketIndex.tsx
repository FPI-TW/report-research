import { mLabel, mColor } from './meta'

interface MarketIndexProps {
  markets: { market: string; count: number }[]
  onPickMarket: (market: string) => void
}

export function MarketIndex({ markets, onPickMarket }: MarketIndexProps) {
  return (
    <div data-testid="market-index">
      <p style={{ padding: '6px 12px', fontSize: 13, color: '#868e96', margin: 0 }}>
        選擇市場分類，檢視該市場的研報
      </p>
      {markets.filter((m) => m.market).map(({ market, count }) => (
        <button
          key={market}
          type="button"
          data-testid="market-index-item"
          data-market={market}
          onClick={() => onPickMarket(market)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            width: '100%',
            padding: '10px 12px',
            background: 'none',
            border: 'none',
            borderBottom: '1px solid #f1f3f5',
            cursor: 'pointer',
            textAlign: 'left',
            fontSize: 14,
          }}
        >
          <span
            aria-hidden="true"
            style={{
              display: 'inline-block',
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: mColor(market),
              flexShrink: 0,
            }}
          />
          <span style={{ flex: 1 }}>{mLabel(market)}</span>
          <span style={{ color: '#868e96', fontSize: 13 }}>{count.toLocaleString()}</span>
          <span aria-hidden="true" style={{ color: '#ced4da' }}>
            ›
          </span>
        </button>
      ))}
    </div>
  )
}
