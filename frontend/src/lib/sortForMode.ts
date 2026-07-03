import type { SearchMode, SortValue } from './searchFilters'

export interface SortOption { value: SortValue; label: string }

const SEARCH_OPTS: SortOption[] = [
  { value: 'relevance', label: '相關度' },
  { value: 'date_desc', label: '日期（新→舊）' },
  { value: 'date_asc', label: '日期（舊→新）' },
]
const BROWSE_OPTS: SortOption[] = [
  { value: 'date_desc', label: '日期（新→舊）' },
  { value: 'date_asc', label: '日期（舊→新）' },
]

export function sortOptions(mode: SearchMode): SortOption[] {
  return mode === 'search' ? SEARCH_OPTS : BROWSE_OPTS
}

/** 模式切換後若當前 sort 在新模式不合法（如 browse 下 relevance），回退該模式預設。 */
export function normalizeSort(mode: SearchMode, sort: SortValue): SortValue {
  if (sortOptions(mode).some(o => o.value === sort)) return sort
  return mode === 'search' ? 'relevance' : 'date_desc'
}
