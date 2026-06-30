import { mLabel } from './meta'

interface ResultsMetaProps {
  mode: 'browse' | 'search'
  q: string
  market: string
  total: number
}

export function ResultsMeta({ mode, q, market, total }: ResultsMetaProps) {
  const marketLabel = mLabel(market) // mLabel('全部') 即回 '全部'

  if (mode === 'search') {
    return (
      <div data-testid="results-meta" style={{ fontSize: 13, color: '#495057' }}>
        「{q}」· {marketLabel} — 找到 {total} 篇研報
      </div>
    )
  }

  return (
    <div data-testid="results-meta" style={{ fontSize: 13, color: '#495057' }}>
      {marketLabel} — {total} 篇
    </div>
  )
}
