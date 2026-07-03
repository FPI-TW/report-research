export type SortValue = 'relevance' | 'date_desc' | 'date_asc'
export type ViewMode = 'cards' | 'table'
export type SearchMode = 'search' | 'browse'

export interface SearchState {
  q: string
  market: string            // 'ALL' | 市場代碼
  instrument_type: string   // '' = 全部
  report_type: string       // '' = 全部
  relates_stock: boolean
  relates_futures: boolean
  sort: SortValue
  view: ViewMode
}

export function defaultState(): SearchState {
  return {
    q: '', market: 'ALL', instrument_type: '', report_type: '',
    relates_stock: false, relates_futures: false, sort: 'date_desc', view: 'cards',
  }
}

export function modeOf(s: Pick<SearchState, 'q'>): SearchMode {
  return s.q.trim() ? 'search' : 'browse'
}

function isSort(v: string | null): v is SortValue {
  return v === 'relevance' || v === 'date_desc' || v === 'date_asc'
}

export function parseParams(search: string): SearchState {
  const p = new URLSearchParams(search)
  const q = p.get('q') ?? ''
  const sortRaw = p.get('sort')
  const defSort: SortValue = q.trim() ? 'relevance' : 'date_desc'
  return {
    q,
    market: p.get('market') || 'ALL',
    instrument_type: p.get('instrument_type') ?? '',
    report_type: p.get('report_type') ?? '',
    relates_stock: p.get('relates_stock') === '1',
    relates_futures: p.get('relates_futures') === '1',
    sort: isSort(sortRaw) ? sortRaw : defSort,
    view: p.get('view') === 'table' ? 'table' : 'cards',
  }
}

export function buildParams(s: SearchState): URLSearchParams {
  const p = new URLSearchParams()
  if (s.q.trim()) p.set('q', s.q.trim())
  if (s.market !== 'ALL') p.set('market', s.market)
  if (s.instrument_type) p.set('instrument_type', s.instrument_type)
  if (s.report_type) p.set('report_type', s.report_type)
  if (s.relates_stock) p.set('relates_stock', '1')
  if (s.relates_futures) p.set('relates_futures', '1')
  const defSort: SortValue = s.q.trim() ? 'relevance' : 'date_desc'
  if (s.sort !== defSort) p.set('sort', s.sort)
  if (s.view !== 'cards') p.set('view', s.view)
  return p
}

export function toApiParams(s: SearchState, page: { limit: number; offset: number }): URLSearchParams {
  const p = new URLSearchParams()
  if (modeOf(s) === 'search') p.set('q', s.q.trim())
  if (s.market !== 'ALL') p.set('market', s.market)
  if (s.instrument_type) p.set('instrument_type', s.instrument_type)
  if (s.report_type) p.set('report_type', s.report_type)
  if (s.relates_stock) p.set('relates_stock', 'true')
  if (s.relates_futures) p.set('relates_futures', 'true')
  p.set('sort', s.sort)
  p.set('limit', String(page.limit))
  p.set('offset', String(page.offset))
  return p
}

export function activeAdvancedCount(s: SearchState): number {
  let n = 0
  if (s.instrument_type) n++
  if (s.report_type) n++
  if (s.relates_stock) n++
  if (s.relates_futures) n++
  return n
}

export function hasAnyFilter(s: SearchState): boolean {
  return s.market !== 'ALL' || activeAdvancedCount(s) > 0
}

export function clearFilters(s: SearchState): SearchState {
  return {
    ...s, market: 'ALL', instrument_type: '', report_type: '',
    relates_stock: false, relates_futures: false,
  }
}

export function browseAll(s: SearchState): SearchState {
  return { ...clearFilters(s), q: '', sort: 'date_desc' }
}
