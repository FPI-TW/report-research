import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { getJSON } from './api'
import { conversationSummarySchema, type ConversationSummary } from './schemas'

export function useConversations() {
  return useQuery<ConversationSummary[]>({
    queryKey: ['conversations'],
    queryFn: () =>
      getJSON('/api/conversations?limit=50', z.array(conversationSummarySchema), { cache: 'no-store' }),
  })
}
