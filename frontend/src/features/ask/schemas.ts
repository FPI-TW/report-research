import { z } from 'zod'

export const sourceSchema = z.object({
  n: z.number().int(),
  report_id: z.string(),
  file_name: z.string(),
  market: z.string().nullish(),
  report_date: z.string().nullish(),
  is_latest: z.boolean().nullish(),
})

export const extSourceSchema = z.object({
  title: z.string().nullish(),
  url: z.string(),
})

export const conversationSummarySchema = z.object({
  conversation_id: z.string(),
  title: z.string(),
  last_at: z.string().nullish(),
  turn_count: z.number().int().nonnegative().nullish(),
})
export type ConversationSummary = z.infer<typeof conversationSummarySchema>

export const historyItemSchema = z.object({
  id: z.string(),
  question: z.string(),
  answer: z.string().nullish(),
  created_at: z.string().nullish(),
  feedback: z.string().nullish(),
  sources: z.array(sourceSchema).nullish(),
  ext_sources: z.array(extSourceSchema).nullish(),
  is_offtopic: z.boolean().nullish(),
  thinking_ms: z.number().nullish(),
  reports: z.array(z.unknown()).nullish(),
})
export type HistoryItem = z.infer<typeof historyItemSchema>

export const okSchema = z.object({ ok: z.boolean() })
