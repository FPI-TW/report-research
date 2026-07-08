import { marketLabel } from './meta'
import { hasAnyFilter, type SearchState } from './searchFilters'

export function resultsMetaText(s: SearchState, total: number): string {
  const mkt = s.market !== 'ALL' ? marketLabel(s.market) : null
  if (s.q.trim()) {
    const mktPart = mkt ? ` · ${mkt}` : ''
    return `「${s.q.trim()}」${mktPart} — 找到 ${total} 篇研報`
  }
  if (hasAnyFilter(s)) return `${mkt ?? '全部研報'} — ${total} 篇`
  return `全部研報 — 共 ${total} 篇`
}

export interface EmptyStateCopy {
  title: string
  hint: string
  showClear: boolean
  showBrowseAll: boolean
}

export function emptyState(s: SearchState, hasFilter: boolean): EmptyStateCopy {
  const isSearch = !!s.q.trim()
  return {
    title: isSearch ? `找不到「${s.q.trim()}」的相關研報` : '沒有符合條件的研報',
    hint: '換個說法或關鍵字試試，或清除目前的篩選條件重新檢索。',
    showClear: hasFilter,
    showBrowseAll: isSearch,
  }
}
