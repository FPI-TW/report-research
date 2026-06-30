import type { Row } from '../lib/normalize'

// 表格「標的」欄摘要（純文字；個股/期貨各列前三、超過加 …；無標的回 —）
export function targetsSummary(r: Row): string {
  const parts: string[] = []
  if (r.relates_stock) {
    const t = (r.stock_targets ?? []).filter(Boolean)
    parts.push(t.length ? `個股 ${t.slice(0, 3).join('、')}${t.length > 3 ? '…' : ''}` : '個股')
  }
  if (r.relates_futures) {
    const t = (r.futures_targets ?? []).filter(Boolean)
    parts.push(t.length ? `期貨 ${t.slice(0, 3).join('、')}${t.length > 3 ? '…' : ''}` : '期貨')
  }
  return parts.length ? parts.join('　') : '—'
}
