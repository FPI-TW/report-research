import { z } from 'zod'

export const marketCountSchema = z.object({ market: z.string().nullable(), count: z.number() })
export const taggingSchema = z.object({ done: z.number(), total: z.number(), fail: z.number(), pct: z.number() })
export const ingestSchema = z.object({ ingested: z.number(), chunks: z.number(), fail: z.number() })
export const summarySchema = z.object({ done: z.number(), total: z.number(), remaining: z.number(), pct: z.number() })
export const pipelinesSchema = z.object({ web: z.boolean(), ingest: z.boolean(), tag: z.boolean(), summaries: z.boolean() })
export const orchestratorSchema = z.object({
  raw: z.string(),
  timestamp: z.string().nullable(),
  status: z.string(),
  label: z.string(),
})

export const progressSchema = z.object({
  ts: z.string(),
  db: z.object({
    reports: z.number(),
    chunks: z.number(),
    markets: z.array(marketCountSchema),
  }),
  summary: summarySchema,
  tagging: taggingSchema.nullable(),
  ingest: ingestSchema.nullable(),
  pipelines: pipelinesSchema,
  orchestrator: orchestratorSchema.nullable(),
})

export type Progress = z.infer<typeof progressSchema>
export type Tagging = z.infer<typeof taggingSchema>
export type Ingest = z.infer<typeof ingestSchema>
export type Summary = z.infer<typeof summarySchema>
export type Pipelines = z.infer<typeof pipelinesSchema>
export type MarketCount = z.infer<typeof marketCountSchema>
export type Orchestrator = z.infer<typeof orchestratorSchema>
