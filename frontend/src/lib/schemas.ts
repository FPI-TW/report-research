import { z } from 'zod'

const marketSchema = z.object({ market: z.string(), count: z.number() })

export const progressSchema = z.object({
  ts: z.string(),
  db: z.object({
    reports: z.number(),
    chunks: z.number(),
    markets: z.array(marketSchema),
  }),
  summary: z.object({
    done: z.number(),
    total: z.number(),
    remaining: z.number(),
    pct: z.number(),
  }),
  tagging: z
    .object({ pct: z.number(), done: z.number(), total: z.number(), fail: z.number() })
    .nullish(),
  ingest: z.object({ ingested: z.number(), fail: z.number() }).nullish(),
  pipelines: z
    .object({ web: z.boolean(), ingest: z.boolean(), tag: z.boolean(), summaries: z.boolean() })
    .partial()
    .nullish(),
  orchestrator: z
    .object({
      label: z.string().nullish(),
      status: z.string().nullish(),
      timestamp: z.string().nullish(),
      raw: z.string().nullish(),
    })
    .nullish(),
})

export type ProgressResponse = z.infer<typeof progressSchema>
