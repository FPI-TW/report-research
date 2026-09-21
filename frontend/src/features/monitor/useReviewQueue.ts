import { useInfiniteQuery } from '@tanstack/react-query'
import { getJSON } from '../../lib/api'
import { reviewQueueSchema, type ReviewItem, type ReviewKind } from '../../lib/reviewSchemas'

const PAGE_SIZE = 10

export interface UseReviewQueue {
  items: ReviewItem[]
  total: number
  minScore: number | null
  isLoading: boolean
  isError: boolean
  hasMore: boolean
  isFetchingMore: boolean
  loadMore: () => void
  refetch: () => void
}

/**
 * 待複核佇列的一個分頁籤。
 *
 * 刻意**不跟著監控頁的 5 秒輪詢走**：佇列是給人逐筆點進去看的，清單在手上被換掉
 * 比晚一分鐘看到新項目糟得多。staleTime 60 秒＋切換分頁籤／重新進頁才更新。
 */
export function useReviewQueue(kind: ReviewKind): UseReviewQueue {
  const query = useInfiniteQuery({
    queryKey: ['review-queue', kind],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      getJSON(`/api/review/queue?kind=${kind}&limit=${PAGE_SIZE}&offset=${pageParam}`, reviewQueueSchema),
    getNextPageParam: last => last.next_offset ?? undefined,
    staleTime: 60_000,
    retry: false,
  })
  const pages = query.data?.pages ?? []
  return {
    items: pages.flatMap(p => p.items),
    total: pages[0]?.total ?? 0,
    minScore: pages[0]?.min_score ?? null,
    isLoading: query.isLoading,
    isError: query.isError,
    hasMore: Boolean(query.hasNextPage),
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => { void query.fetchNextPage() },
    refetch: () => { void query.refetch() },
  }
}
