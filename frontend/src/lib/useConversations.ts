import { useInfiniteQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { getJSON } from './api'
import { conversationSummarySchema, type ConversationSummary } from './schemas'

export const CONVERSATIONS_PAGE_SIZE = 50

export interface UseConversations {
  items: ConversationSummary[]
  isLoading: boolean
  isError: boolean
  hasMore: boolean
  isFetchingMore: boolean
  loadMore: () => void
}

/**
 * 對話串清單，支援搜尋與「載入更多」。
 *
 * 先前固定只抓最新 50 串、沒有搜尋：第 51 串之後的對話在畫面上完全不存在，要找回舊答案
 * 只能憑記憶往下翻。`q` 比對的是整串的提問（後端），不只是清單上顯示的那個標題。
 *
 * 端點回的是裸陣列、沒有 total（刻意的，見 web/routers/qa_history.py），所以「還有沒有
 * 下一頁」以「這一頁是不是滿的」判斷。最壞情況是總數剛好是頁大小的整數倍時多抓一次空頁。
 *
 * queryKey 第二格放搜尋詞：送出問題或刪除對話之後的
 * `invalidateQueries({ queryKey: ['conversations'] })` 是前綴比對，搜尋中的清單也會一起更新。
 */
export function useConversations(q = ''): UseConversations {
  const needle = q.trim()
  const query = useInfiniteQuery({
    queryKey: ['conversations', needle],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => {
      const sp = new URLSearchParams({ limit: String(CONVERSATIONS_PAGE_SIZE), offset: String(pageParam) })
      if (needle) sp.set('q', needle)
      return getJSON(`/api/conversations?${sp}`, z.array(conversationSummarySchema), { cache: 'no-store' })
    },
    getNextPageParam: (lastPage, allPages) =>
      lastPage.length === CONVERSATIONS_PAGE_SIZE
        ? allPages.reduce((n, p) => n + p.length, 0)
        : undefined,
  })
  return {
    items: query.data?.pages.flat() ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
    hasMore: Boolean(query.hasNextPage),
    isFetchingMore: query.isFetchingNextPage,
    loadMore: () => { void query.fetchNextPage() },
  }
}
