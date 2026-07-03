import { z } from 'zod'

export const statsSchema = z.object({
  total_reports: z.number().int().nonnegative(),
  total_chunks: z.number().int().nonnegative(),
  markets: z.array(z.object({ market: z.string(), count: z.number().int().nonnegative() })),
  instrument_types: z.array(z.object({ type: z.string(), count: z.number().int().nonnegative() })),
  report_types: z.array(z.object({ type: z.string(), count: z.number().int().nonnegative() })),
  username: z.string().nullish(),
})
export type StatsResponse = z.infer<typeof statsSchema>

export const conversationSummarySchema = z.object({
  conversation_id: z.string(),
  title: z.string(),
  last_at: z.string().nullish(),
  turn_count: z.number().int().nonnegative().nullish(),
})
export type ConversationSummary = z.infer<typeof conversationSummarySchema>
