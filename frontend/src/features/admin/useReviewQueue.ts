import { useInfiniteQuery, useMutation, useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { getJSON } from '../../lib/api'
import {
  reviewQueueSchema, reviewStateSchema,
  type ReviewItem, type ReviewKind, type ReviewQueue, type ReviewStatus, type ReviewVerification,
} from '../../lib/reviewSchemas'

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
  save: (id: string, update: { status: ReviewStatus; note: string; verification: ReviewVerification }) => Promise<void>
  isSaving: boolean
}

/**
 * 待複核佇列的一個分頁籤。
 *
 * 刻意**不跟著監控頁的 5 秒輪詢走**：佇列是給人逐筆點進去看的，清單在手上被換掉
 * 比晚一分鐘看到新項目糟得多。staleTime 60 秒＋切換分頁籤／重新進頁才更新。
 */
export function useReviewQueue(kind: ReviewKind, status: ReviewStatus | 'all' = 'open'): UseReviewQueue {
  const client = useQueryClient()
  const query = useInfiniteQuery({
    queryKey: ['review-queue', kind, status],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      getJSON(`/api/review/queue?kind=${kind}&status=${status}&limit=${PAGE_SIZE}&offset=${pageParam}`, reviewQueueSchema),
    getNextPageParam: last => last.next_offset ?? undefined,
    staleTime: 60_000,
    retry: false,
  })
  const mutation = useMutation({
    mutationFn: ({ id, update }: { id: string; update: { status: ReviewStatus; note: string; verification: ReviewVerification } }) =>
      getJSON(`/api/review/${kind}/${encodeURIComponent(id)}`, reviewStateSchema, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(update),
      }),
    onSuccess: async saved => {
      const filter = { queryKey: ['review-queue', saved.kind] }
      await client.cancelQueries(filter)
      // 狀態變更會讓 OFFSET 位移；回到首頁重新取數，避免跳過項目，
      // 也避免 infinite query 逐頁重查所有已展開的頁面。
      client.setQueriesData<InfiniteData<ReviewQueue, number>>(filter, data => data && ({
        pages: data.pages.slice(0, 1),
        pageParams: data.pageParams.slice(0, 1),
      }))
      await client.invalidateQueries(filter)
    },
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
    save: async (id, update) => { await mutation.mutateAsync({ id, update }) },
    isSaving: mutation.isPending,
  }
}
