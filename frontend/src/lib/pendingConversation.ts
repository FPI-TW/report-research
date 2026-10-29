import { queryOptions, useQuery, type QueryClient } from '@tanstack/react-query'

/**
 * 新對話送出第一題、後端還沒落庫之前，側欄「最近」先列出的那幾串。
 *
 * 對話串的 `conversation_id` 由後端在整輪落庫時才決定（跑完的 `done`，或沒跑完時 `/api/ask/stop`
 * 回的 qa_id），清單要等那時重抓才會有這一串；一題常要跑數十秒，這段期間使用者在側欄看不到自己
 * 剛開的對話。所以送出當下先在前端記一筆，`ConversationList` 把它併到清單最上面，重抓到真的那一列
 * 之後就清掉。
 *
 * 是清單而不是單一一筆：跑到一半切去開新對話時，舊的那一串還在補存（useAskController 的
 * keepUnfinished），新的一串已經送出，兩筆會同時存在。
 *
 * 存在 react-query 的快取裡（不是 module 級變數）：側欄與問答頁本來就共用同一個 QueryClient，
 * 測試每題各建一個也就互不污染。鍵刻意不以 `conversations` 開頭——送出與刪除後的
 * `invalidateQueries({ queryKey: ['conversations'] })` 是前綴比對，會把它一起失效掉。
 */
export interface PendingConversation {
  /** 該輪的 turn id：清除與改連時用它找自己那一筆。 */
  key: string
  title: string
  /** 落庫之後才知道；null＝後端還沒建立這一串。 */
  conversationId: string | null
}

const pendingQuery = queryOptions<PendingConversation[]>({
  queryKey: ['pendingConversations'],
  queryFn: () => [],
  // 只由 setQueryData 寫入，永遠不去抓。
  enabled: false,
  initialData: [],
  staleTime: Infinity,
})
const KEY = pendingQuery.queryKey

/** 新的在前。 */
export function usePendingConversations(): PendingConversation[] {
  return useQuery(pendingQuery).data ?? []
}

export function startPendingConversation(qc: QueryClient, key: string, title: string): void {
  const entry: PendingConversation = { key, title, conversationId: null }
  qc.setQueryData(KEY, prev => [entry, ...(prev ?? []).filter(p => p.key !== key)])
}

/** 拿到真的 conversation_id：清單列改連到那一串，重抓到之後由真的那列接手。 */
export function resolvePendingConversation(qc: QueryClient, key: string, conversationId: string): void {
  qc.setQueryData(KEY, prev => (prev ?? []).map(p => (p.key === key ? { ...p, conversationId } : p)))
}

/**
 * 清掉自己那一筆。`unresolvedOnly` 用在「這一輪沒拿到 conversation_id 就結束」：若先前已收到 done
 * （之後才被停止或取代），那一筆要留到清單重抓完。
 */
export function clearPendingConversation(qc: QueryClient, key: string, { unresolvedOnly = false } = {}): void {
  qc.setQueryData(KEY, prev => (prev ?? []).filter(p => p.key !== key || (unresolvedOnly && p.conversationId !== null)))
}
