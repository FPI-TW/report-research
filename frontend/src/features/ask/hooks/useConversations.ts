import { useCallback } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteConversation, getConversations } from '../api'
import type { ConversationSummary } from '../schemas'

export interface UseConversations {
  conversations: ConversationSummary[]
  isLoading: boolean
  isError: boolean
  remove: (id: string) => void
  refresh: () => void
}

export function useConversations(): UseConversations {
  const qc = useQueryClient()
  const list = useQuery({
    queryKey: ['conversations'],
    queryFn: getConversations,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const del = useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['conversations'] })
    },
  })
  // refresh 必須穩定（AskPage 以它作 useEffect 依賴；不穩定會每次 render 觸發失效迴圈）
  const refresh = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['conversations'] })
  }, [qc])
  return {
    conversations: list.data ?? [],
    isLoading: list.isLoading,
    isError: list.isError,
    remove: del.mutate,
    refresh,
  }
}
