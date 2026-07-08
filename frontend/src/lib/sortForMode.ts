import type { SearchMode, SortValue } from './searchFilters'

export interface SortOption { value: SortValue; label: string }

// 已移除日期排序可選項：搜尋只留「相關度」；瀏覽無可選排序（固定新→舊，見 normalizeSort 預設）。
const SEARCH_OPTS: SortOption[] = [
  { value: 'relevance', label: '相關度' },
]
const BROWSE_OPTS: SortOption[] = []

export function sortOptions(mode: SearchMode): SortOption[] {
  return mode === 'search' ? SEARCH_OPTS : BROWSE_OPTS
}

/**
 * 將 sort 正規化為該模式的合法值：不在可選清單者回退預設
 * （search→relevance、browse→date_desc）。日期選項移除後，
 * search 一律 relevance、browse 一律 date_desc（順序不變，只是不可選）。
 */
export function normalizeSort(mode: SearchMode, sort: SortValue): SortValue {
  if (sortOptions(mode).some(o => o.value === sort)) return sort
  return mode === 'search' ? 'relevance' : 'date_desc'
}
