import { z } from 'zod'
import { MARKET_ORDER } from './meta'

export const marketSchema = z.enum(MARKET_ORDER)
export type Market = z.infer<typeof marketSchema>

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
  /** 報告內部標題（顯示用）；缺值＝回退 file_name。 */
  title: z.string().nullish(),
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
  reason_code: z.string().nullish(),
  incomparable_reason: z.string().nullish(),
})
export type ChangeItem = z.infer<typeof changeItemSchema>

export const ratingBucketCountSchema = z.object({
  rating: ratingNormSchema,
  count: z.number().int(),
})
export type RatingBucketCount = z.infer<typeof ratingBucketCountSchema>

const FIVE_LEVEL_RATINGS = ['buy', 'overweight', 'neutral', 'underweight', 'sell'] as const
const ratingDistributionSchema = z.array(ratingBucketCountSchema).length(5).superRefine((items, ctx) => {
  const keys = items.map(item => item.rating)
  if (new Set(keys).size !== FIVE_LEVEL_RATINGS.length
      || FIVE_LEVEL_RATINGS.some(key => !keys.includes(key))) {
    ctx.addIssue({ code: 'custom', message: '評等分布必須完整且不得重複' })
  }
})

export const ratingConsensusSchema = z.object({
  distribution: ratingDistributionSchema,
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

const THESIS_DIMENSIONS = ['outlook', 'catalyst', 'risk', 'valuation'] as const
const thesisDimensionsSchema = z.array(thesisDimensionSchema).length(4).superRefine((items, ctx) => {
  const keys = items.map(item => item.dimension)
  if (new Set(keys).size !== THESIS_DIMENSIONS.length
      || THESIS_DIMENSIONS.some(key => !keys.includes(key))) {
    ctx.addIssue({ code: 'custom', message: '四向觀點必須完整且不得重複' })
  }
})

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
  latest_eps_period: z.string().nullish(),
  latest_eps_currency: z.string().nullish(),
  latest_eps_unit: z.string().nullish(),
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
  market: marketSchema,
  market_display: z.string().nullish(),
  instrument_code: z.string(),
  instrument_name: z.string().nullish(),
  window: windowSchema,
  as_of: z.string().nullish(),
  coverage: coverageSchema,
  rating: ratingConsensusSchema.nullish(),
  target_price: targetConsensusSchema.nullish(),
  eps: epsConsensusSchema.nullish(),
  thesis: thesisDimensionsSchema,
  recent_events: z.array(eventCardSchema),
  recent_events_total: z.number().int(),
  recent_events_has_more: z.boolean().optional(),
  recent_events_next_offset: z.number().int().nonnegative().nullish(),
  brokers: z.array(brokerSummarySchema),
  notes: z.array(z.string()),
}).transform((overview) => {
  const recentEventsHasMore = overview.recent_events_has_more
    ?? (overview.recent_events_next_offset != null
      || overview.recent_events_total > overview.recent_events.length)
  const recentEventsNextOffset = overview.recent_events_next_offset === undefined
    ? (recentEventsHasMore ? overview.recent_events.length : null)
    : overview.recent_events_next_offset

  return {
    ...overview,
    recent_events_has_more: recentEventsHasMore,
    recent_events_next_offset: recentEventsNextOffset,
  }
})
export type RadarOverview = z.infer<typeof radarOverviewSchema>

export const thesisCellSchema = z.object({
  dimension: dimKeySchema,
  dimension_display: z.string(),
  stance: z.string().nullish(),
  summary: z.string().nullish(),
  evidence: z.string().nullish(),
})

const thesisCellsSchema = z.array(thesisCellSchema).length(4).superRefine((items, ctx) => {
  const keys = items.map(item => item.dimension)
  if (new Set(keys).size !== THESIS_DIMENSIONS.length
      || THESIS_DIMENSIONS.some(key => !keys.includes(key))) {
    ctx.addIssue({ code: 'custom', message: '券商四向觀點必須完整且不得重複' })
  }
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
  primary_eps: epsGroupSchema.nullish(),
  thesis: thesisCellsSchema,
  extraction_status: z.string(),
  report_link: reportLinkSchema,
})
export type BrokerSnapshot = z.infer<typeof brokerSnapshotSchema>

export const snapshotDiffSchema = z.object({
  from_report_id: z.string().nullish(),
  from_report_date: z.string().nullish(),
  to_report_date: z.string(),
  changes: z.array(changeItemSchema),
  has_prior_report: z.boolean().optional(),
  has_prior_comparable: z.boolean(),
  note: z.string().nullish(),
})
export type SnapshotDiff = z.infer<typeof snapshotDiffSchema>

export const brokerHistorySchema = z.object({
  market: marketSchema,
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
  distribution: ratingDistributionSchema,
  upgrades: z.number().int(),
  downgrades: z.number().int(),
  net_rating: z.number().int(),
})
export type InstrumentStance = z.infer<typeof instrumentStanceSchema>

/**
 * 清單頁的目標價摘要：**只有方向**。
 *
 * 後端刻意不再回中位數／幣別／修正幅度（見 `app/services/radar/schemas.py` 的
 * `InstrumentTargetBrief`）。這裡跟著只宣告方向——多宣告一個 `median` 也拿不到值，
 * 只會讓下一個讀這段的人以為那個數字存在。`target` 為 null ＝沒有任何一家給目標價。
 */
export const instrumentTargetBriefSchema = z.object({
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
  market: marketSchema,
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

/** 目錄排序鍵。與後端 `schemas.CatalogSort` 逐字鏡像（值直接進 SQL 白名單）。 */
export const catalogSortSchema = z.enum(['latest', 'reports', 'brokers', 'code'])
export type CatalogSort = z.infer<typeof catalogSortSchema>

/** 立場三桶。與後端 `scale.RATING_BUCKET` 的輸出逐字鏡像，不可自造簡寫。 */
export const stanceFilterSchema = z.enum(['bullish', 'neutral', 'bearish'])
export type StanceFilter = z.infer<typeof stanceFilterSchema>

export const radarInstrumentsSchema = z.object({
  total: z.number().int(),
  limit: z.number().int().positive().optional(),
  offset: z.number().int(),
  has_more: z.boolean().optional(),
  next_offset: z.number().int().nonnegative().nullish(),
  items: z.array(radarInstrumentItemSchema),
  // zod 物件預設 strip：沒有這兩條宣告，後端回了也會被安靜丟掉（監控頁的 takeaway／
  // signal 覆蓋率就是這樣從 P4 起一直沒出現）。
  //
  // 用 `.optional()` 而不是 `.default({})`：滾動部署時舊後端還沒回這兩個欄位，
  // 而 `.default()` 會讓推導出的**輸出型別變成必填**，測試裡每一份手寫 fixture 都得補一把。
  // 消費端一律寫 `data.facets ?? {}`。
  facets: z.record(z.string(), z.number().int()).optional(),
  latest_report_date: z.string().nullish(),
})
export type RadarInstruments = z.infer<typeof radarInstrumentsSchema>

export const radarEventsSchema = z.object({
  market: marketSchema,
  instrument_code: z.string(),
  window: windowSchema,
  as_of: z.string().nullish(),
  total: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
  has_more: z.boolean(),
  next_offset: z.number().int().nonnegative().nullable(),
  items: z.array(eventCardSchema),
})
export type RadarEvents = z.infer<typeof radarEventsSchema>
