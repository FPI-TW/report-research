import {
  catalogSortSchema, stanceFilterSchema,
  type CatalogSort, type Market, type StanceFilter,
} from '../../lib/radarSchemas'

/** 共識預覽恆以 90 天窗期計算（後端 `build_instrument_slim()` 不吃 URL 的 `window`）。 */
export const CATALOG_WINDOW_DAYS = 90

export const DEFAULT_SORT: CatalogSort = 'latest'

/** 排序選項。值與後端 `queries.CATALOG_SORTS` 的鍵逐字相同。 */
export const SORT_OPTIONS: ReadonlyArray<{ value: CatalogSort; label: string }> = [
  { value: 'latest', label: '最新研報' },
  { value: 'reports', label: '研報數' },
  { value: 'brokers', label: '券商數' },
  { value: 'code', label: '代碼' },
]

export const SORT_LABEL: Record<CatalogSort, string> = Object.fromEntries(
  SORT_OPTIONS.map(o => [o.value, o.label]),
) as Record<CatalogSort, string>

/** 立場篩選的顯示字。鏡像後端 `radar/scale.py` 的 `BUCKET_DISPLAY`，不另造詞。 */
export const STANCE_DISPLAY: Record<StanceFilter, string> = {
  bullish: '偏多',
  neutral: '中立',
  bearish: '偏空',
}

/** `''` 代表不篩選——原生 `<select>` 的 value 只能是字串，不能是 undefined。 */
export const STANCE_OPTIONS: ReadonlyArray<{ value: StanceFilter | ''; label: string }> = [
  { value: '', label: '全部' },
  { value: 'bullish', label: STANCE_DISPLAY.bullish },
  { value: 'neutral', label: STANCE_DISPLAY.neutral },
  { value: 'bearish', label: STANCE_DISPLAY.bearish },
]

export interface CatalogState {
  q: string
  sort: CatalogSort
  stance: StanceFilter | null
}

/**
 * 網址 → 清單狀態。非法值一律**靜默退回預設**而不是報錯或清空整組條件
 * （與 `RadarPage` 對 `market`／`window` 的既有處置一致）。
 */
export function parseCatalogState(params: URLSearchParams): CatalogState {
  const sort = catalogSortSchema.safeParse(params.get('sort'))
  const stance = stanceFilterSchema.safeParse(params.get('stance'))
  return {
    q: params.get('q') ?? '',
    sort: sort.success ? sort.data : DEFAULT_SORT,
    stance: stance.success ? stance.data : null,
  }
}

/**
 * 清單狀態 → 要寫進網址的補丁。**預設值一律寫成 `null`（＝從網址刪掉）**，
 * 否則每個人分享出去的連結都會帶一串等同於預設的參數，兩種網址指向同一個畫面。
 */
export function catalogPatch(next: Partial<CatalogState>): Record<string, string | null> {
  const patch: Record<string, string | null> = {}
  if (next.q !== undefined) patch.q = next.q.trim() || null
  if (next.sort !== undefined) patch.sort = next.sort === DEFAULT_SORT ? null : next.sort
  if (next.stance !== undefined) patch.stance = next.stance ?? null
  return patch
}

/**
 * 標的詳情頁的連結：以**當前網址**為基底再補 market/code/window。
 *
 * 以當前網址為基底是關鍵——`q`／`sort`／`stance` 因此一路帶著，從詳情頁按返回
 * （`patch({ code: null })`）就自動回到原本的篩選，不必另外記一份狀態。
 */
export function instrumentHref(
  params: URLSearchParams,
  market: Market,
  code: string,
): string {
  const sp = new URLSearchParams(params)
  sp.set('market', market)
  sp.set('code', code)
  // broker（展開哪家券商）是「這一檔」的狀態：帶去另一檔只會指到一家可能根本沒覆蓋它的券商。
  sp.delete('broker')
  if (!sp.get('window')) sp.set('window', '90')
  return `/radar?${sp}`
}
