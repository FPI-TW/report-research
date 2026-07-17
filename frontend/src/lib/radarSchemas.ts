import { z } from 'zod'

/** 與後端 app/services/radar/schemas.py 逐字鏡像的 enum 契約。 */
export const directionSchema = z.enum(['up', 'down', 'flat', 'incomparable', 'none'])
export type Direction = z.infer<typeof directionSchema>

export const ratingNormSchema = z.enum([
  'buy', 'overweight', 'neutral', 'underweight', 'sell', 'unknown',
])
export type RatingNorm = z.infer<typeof ratingNormSchema>

export const dimKeySchema = z.enum(['outlook', 'catalyst', 'risk', 'valuation'])
export type DimKey = z.infer<typeof dimKeySchema>

export const dimLabelSchema = z.enum([
  'strengthen', 'weaken', 'diverging', 'stable', 'insufficient',
])
export type DimLabel = z.infer<typeof dimLabelSchema>

export const coverageStateSchema = z.enum([
  'ok', 'partial', 'pending_extraction', 'window_empty',
])
export type CoverageState = z.infer<typeof coverageStateSchema>

export const windowSchema = z.enum(['30', '90', '180', 'all'])
export type Window = z.infer<typeof windowSchema>

export const reportLinkSchema = z.object({
  report_id: z.string(),
  file_name: z.string().nullish(),
  report_date: z.string().nullish(),
  broker: z.string().nullish(),
  broker_display: z.string().nullish(),
})
export type ReportLink = z.infer<typeof reportLinkSchema>

export const changeItemSchema = z.object({
  field: z.enum(['rating', 'target_price', 'eps', 'thesis']),
  dimension: z.string().nullish(),
  label: z.string(),
  direction: directionSchema,
  prev_value: z.string().nullish(),
  curr_value: z.string().nullish(),
  pct_change: z.number().nullish(),
  comparable: z.boolean(),
  incomparable_reason: z.string().nullish(),
})
export type ChangeItem = z.infer<typeof changeItemSchema>

export const ratingBucketCountSchema = z.object({
  rating: ratingNormSchema,
  count: z.number().int(),
})
export type RatingBucketCount = z.infer<typeof ratingBucketCountSchema>

export const ratingConsensusSchema = z.object({
  distribution: z.array(ratingBucketCountSchema),
  bullish: z.number().int(),
  neutral: z.number().int(),
  bearish: z.number().int(),
  unknown: z.number().int(),
  total_rated: z.number().int(),
  median_rating: ratingNormSchema.nullish(),
  upgrades: z.number().int(),
  downgrades: z.number().int(),
  unchanged: z.number().int(),
})
export type RatingConsensus = z.infer<typeof ratingConsensusSchema>

export const targetGroupSchema = z.object({
  currency: z.string(),
  median: z.number(),
  q1: z.number(),
  q3: z.number(),
  low: z.number(),
  high: z.number(),
  count: z.number().int(),
  revision_pct: z.number().nullish(),
  revision_direction: directionSchema.default('none'),
})
export type TargetGroup = z.infer<typeof targetGroupSchema>

export const targetConsensusSchema = z.object({
  primary_currency: z.string().nullish(),
  groups: z.array(targetGroupSchema),
  note: z.string().nullish(),
})
export type TargetConsensus = z.infer<typeof targetConsensusSchema>

export const epsGroupSchema = z.object({
  fiscal_year: z.number().int().nullish(),
  period: z.string().nullish(),
  currency: z.string().nullish(),
  unit: z.string().nullish(),
  median: z.number(),
  count: z.number().int(),
  revision_pct: z.number().nullish(),
  revision_direction: directionSchema.default('none'),
})
export type EpsGroup = z.infer<typeof epsGroupSchema>

export const epsConsensusSchema = z.object({
  primary: epsGroupSchema.nullish(),
  groups: z.array(epsGroupSchema),
})
export type EpsConsensus = z.infer<typeof epsConsensusSchema>

export const thesisDimensionSchema = z.object({
  dimension: dimKeySchema,
  dimension_display: z.string(),
  label: dimLabelSchema,
  label_display: z.string(),
  brokers_strengthen: z.number().int(),
  brokers_weaken: z.number().int(),
  brokers_comparable: z.number().int(),
  coverage_note: z.string().nullish(),
  sample_summary: z.string().nullish(),
})
export type ThesisDimension = z.infer<typeof thesisDimensionSchema>

export const eventCardSchema = z.object({
  broker: z.string().nullish(),
  broker_display: z.string().nullish(),
  report_date: z.string(),
  headline: z.string(),
  changes: z.array(changeItemSchema),
  evidence: z.array(z.string()),
  report_link: reportLinkSchema,
})
export type EventCard = z.infer<typeof eventCardSchema>

export const brokerSummarySchema = z.object({
  broker: z.string().nullish(),
  broker_display: z.string().nullish(),
  latest_rating: ratingNormSchema,
  latest_rating_raw: z.string().nullish(),
  latest_target_price: z.number().nullish(),
  latest_target_currency: z.string().nullish(),
  latest_eps_value: z.number().nullish(),
  latest_eps_fy: z.number().int().nullish(),
  latest_report_date: z.string(),
  report_link: reportLinkSchema,
  recent_change_label: z.string().nullish(),
  recent_change_direction: directionSchema.default('none'),
  stale: z.boolean(),
  has_history: z.boolean(),
})
export type BrokerSummary = z.infer<typeof brokerSummarySchema>

export const coverageSchema = z.object({
  state: coverageStateSchema,
  brokers_total: z.number().int(),
  brokers_extracted: z.number().int(),
  brokers_in_consensus: z.number().int(),
  reports_available: z.number().int(),
  note: z.string(),
})
export type Coverage = z.infer<typeof coverageSchema>

export const radarOverviewSchema = z.object({
  market: z.string(),
  market_display: z.string().nullish(),
  instrument_code: z.string(),
  instrument_name: z.string().nullish(),
  window: windowSchema,
  as_of: z.string().nullish(),
  coverage: coverageSchema,
  rating: ratingConsensusSchema.nullish(),
  target_price: targetConsensusSchema.nullish(),
  eps: epsConsensusSchema.nullish(),
  thesis: z.array(thesisDimensionSchema),
  recent_events: z.array(eventCardSchema),
  recent_events_total: z.number().int(),
  brokers: z.array(brokerSummarySchema),
  notes: z.array(z.string()),
})
export type RadarOverview = z.infer<typeof radarOverviewSchema>

export const thesisCellSchema = z.object({
  dimension: dimKeySchema,
  dimension_display: z.string(),
  stance: z.string().nullish(),
  summary: z.string().nullish(),
  evidence: z.string().nullish(),
})

export const brokerSnapshotSchema = z.object({
  report_id: z.string(),
  report_date: z.string(),
  in_window: z.boolean(),
  rating: ratingNormSchema,
  rating_raw: z.string().nullish(),
  target_price: z.number().nullish(),
  target_currency: z.string().nullish(),
  eps: z.array(epsGroupSchema),
  thesis: z.array(thesisCellSchema),
  extraction_status: z.string(),
  report_link: reportLinkSchema,
})
export type BrokerSnapshot = z.infer<typeof brokerSnapshotSchema>

export const snapshotDiffSchema = z.object({
  from_report_date: z.string().nullish(),
  to_report_date: z.string(),
  changes: z.array(changeItemSchema),
  has_prior_comparable: z.boolean(),
  note: z.string().nullish(),
})
export type SnapshotDiff = z.infer<typeof snapshotDiffSchema>

export const brokerHistorySchema = z.object({
  market: z.string(),
  instrument_code: z.string(),
  broker: z.string().nullish(),
  broker_display: z.string().nullish(),
  window: windowSchema,
  as_of: z.string().nullish(),
  current_rating: ratingNormSchema,
  report_count: z.number().int(),
  snapshots: z.array(brokerSnapshotSchema),
  diffs: z.array(snapshotDiffSchema),
  coverage_state: coverageStateSchema,
})
export type BrokerHistory = z.infer<typeof brokerHistorySchema>

export const instrumentStanceSchema = z.object({
  rating: ratingNormSchema,
  bullish: z.number().int(),
  neutral: z.number().int(),
  bearish: z.number().int(),
  total_rated: z.number().int(),
  distribution: z.array(ratingBucketCountSchema),
  upgrades: z.number().int(),
  downgrades: z.number().int(),
  net_rating: z.number().int(),
})
export type InstrumentStance = z.infer<typeof instrumentStanceSchema>

export const instrumentTargetBriefSchema = z.object({
  currency: z.string(),
  median: z.number(),
  revision_pct: z.number().nullish(),
  revision_direction: directionSchema.default('none'),
})
export type InstrumentTargetBrief = z.infer<typeof instrumentTargetBriefSchema>

export const instrumentConsensusSchema = z.object({
  window: windowSchema,
  stance: instrumentStanceSchema,
  target: instrumentTargetBriefSchema.nullish(),
})
export type InstrumentConsensus = z.infer<typeof instrumentConsensusSchema>

export const radarInstrumentItemSchema = z.object({
  market: z.string(),
  market_display: z.string().nullish(),
  instrument_code: z.string(),
  instrument_name: z.string().nullish(),
  broker_count: z.number().int(),
  report_count: z.number().int(),
  latest_report_date: z.string().nullish(),
  coverage_state: coverageStateSchema,
  consensus: instrumentConsensusSchema.nullish(),
})
export type RadarInstrumentItem = z.infer<typeof radarInstrumentItemSchema>

export const radarInstrumentsSchema = z.object({
  total: z.number().int(),
  offset: z.number().int(),
  items: z.array(radarInstrumentItemSchema),
})
export type RadarInstruments = z.infer<typeof radarInstrumentsSchema>
