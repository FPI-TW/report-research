import { useInfiniteQuery } from '@tanstack/react-query'
import { browseReports, searchReports } from './searchApi'
import { modeOf, toApiParams, type SearchState } from './searchFilters'
import type { MarketFacet, ReportRow } from './schemas'

export const SEARCH_PAGE_SIZE = 50

interface Page { rows: ReportRow[]; total: number; facets: MarketFacet[] }

/** query key：只放會影響「抓取結果」的欄位；view/tableSort 為呈現態，刻意排除以免切檢視就重抓。 */
function resultsKey(s: SearchState) {
  return [
    'search-results', s.q.trim(), s.market, s.instrument_type, s.report_type,
    s.relates_stock, s.relates_futures, s.sort,
  ] as const
}

async function fetchPage(s: SearchState, offset: number): Promise<Page> {
  const params = toApiParams(s, { limit: SEARCH_PAGE_SIZE, offset })
  if (modeOf(s) === 'search') {
    const r = await searchReports(params)
    return { rows: r.results, total: r.total, facets: r.market_facets }
  }
  const r = await browseReports(params)
  return { rows: r.items, total: r.total, facets: [] }
}

export interface UseSearchResults {
  rows: ReportRow[]
  total: number
  /** 命中集合的市場組成；瀏覽態為空陣列（改用 /api/stats 的全庫計數）。 */
  facets: MarketFacet[]
  isLoading: boolean
  isError: boolean
  refetch: () => void
  hasMore: boolean
  remaining: number
  isFetchingMore: boolean
  loadMore: () => void
}

export function useSearchResults(s: SearchState): UseSearchResults {
  const query = useInfiniteQuery({
    queryKey: resultsKey(s),
    initialPageParam: 0,
    queryFn: ({ pageParam }) => fetchPage(s, pageParam),
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((n, p) => n + p.rows.length, 0)
      return loaded < lastPage.total ? loaded : undefined
    },
  })
  const pages = query.data?.pages ?? []
  const rows = pages.flatMap(p => p.rows)
  const total = pages[0]?.total ?? 0
  return {
    rows,
    total,
    facets: pages[0]?.facets ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
    refetch: () => { void query.refetch() },
    hasMore: query.hasNextPage,
    remaining: Math.max(0, total - rows.length),
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => { void query.fetchNextPage() },
  }
}
