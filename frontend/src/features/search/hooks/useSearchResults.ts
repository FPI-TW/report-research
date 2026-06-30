import { useInfiniteQuery } from '@tanstack/react-query'
import { getReports, getSearch } from '../api'
import { normalizeItem, normalizeResult, type Row } from '../lib/normalize'
import type { Filters } from '../lib/filters'

const PAGE = 50

export function useSearchResults(filters: Filters) {
  const mode: 'browse' | 'search' = filters.q ? 'search' : 'browse'

  const q = useInfiniteQuery({
    queryKey: ['search-results', filters],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const base = {
        market: filters.market,
        instrument_type: filters.instrument,
        relates_stock: filters.stock || undefined,
        relates_futures: filters.futures || undefined,
        report_type: filters.type,
        sort: filters.sort,
        limit: PAGE,
        offset: pageParam as number,
      }
      return mode === 'search'
        ? { ...(await getSearch({ ...base, q: filters.q, passages: 4 })), kind: 'search' as const }
        : { ...(await getReports(base)), kind: 'browse' as const }
    },
    getNextPageParam: (last, all) => {
      const loaded = all.reduce(
        (n, p) => n + (p.kind === 'search' ? p.results.length : p.items.length),
        0,
      )
      return loaded < last.total ? loaded : undefined
    },
    retry: false,
    refetchOnWindowFocus: false,
  })

  const rows: Row[] = (q.data?.pages ?? []).flatMap((p) =>
    p.kind === 'search' ? p.results.map(normalizeResult) : p.items.map(normalizeItem),
  )

  const total = q.data?.pages[0]?.total ?? 0

  return {
    rows,
    total,
    mode,
    isLoading: q.isLoading,
    isError: q.isError,
    hasMore: Boolean(q.hasNextPage),
    fetchNextPage: q.fetchNextPage,
    isFetchingNextPage: q.isFetchingNextPage,
    refetch: q.refetch,
  }
}
