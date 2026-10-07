// 自動產生，請勿手改。來源：FastAPI OpenAPI 的 /api/admin/*（後端 pydantic model 即契約）。
// 重新產生：uv run python scripts/gen_admin_client.py（tests/test_admin_client_generated.py 檢查是否最新）
import { z } from 'zod'
import { jsonBody, requestJSON } from '../api'

export const AdminReportItemSchema = z.object({
  report_id: z.string(),
  file_hash: z.string(),
  file_name: z.string(),
  title: z.string().nullable().optional(),
  source: z.string().nullable().optional(),
  market: z.string().nullable().optional(),
  report_date: z.string().nullable().optional(),
  created_at: z.string().nullable().optional(),
  hidden: z.boolean().optional(),
  hidden_reason: z.string().nullable().optional(),
  visibility_updated_by: z.string().nullable().optional(),
  visibility_updated_at: z.string().nullable().optional(),
  publication: z.enum(['draft', 'published']).optional(),
})
export type AdminReportItem = z.infer<typeof AdminReportItemSchema>

export const AdminReportListResponseSchema = z.object({
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
  has_more: z.boolean(),
  next_offset: z.number().int().nullable(),
  items: z.array(AdminReportItemSchema),
})
export type AdminReportListResponse = z.infer<typeof AdminReportListResponseSchema>

export const AdminUploadSchema = z.object({
  upload_id: z.string(),
  file_hash: z.string(),
  original_name: z.string(),
  size_bytes: z.number().int(),
  client_mtime: z.string().nullable().optional(),
  uploaded_by: z.string().nullable().optional(),
  uploaded_at: z.string(),
  state: z.enum(['quarantined', 'scanning', 'clean', 'infected', 'blocked', 'processing', 'draft', 'failed', 'duplicate', 'published', 'rejected']),
  state_changed_at: z.string(),
  scan_attempts: z.number().int().optional(),
  scan_engine: z.string().nullable().optional(),
  scan_signature: z.string().nullable().optional(),
  scanned_at: z.string().nullable().optional(),
  scan_last_error: z.string().nullable().optional(),
  process_attempts: z.number().int().optional(),
  failure_kind: z.string().nullable().optional(),
  failure_detail: z.string().nullable().optional(),
  processed_at: z.string().nullable().optional(),
  decided_by: z.string().nullable().optional(),
  decided_at: z.string().nullable().optional(),
  decision_reason: z.string().nullable().optional(),
  purge_after: z.string().nullable().optional(),
  purged_at: z.string().nullable().optional(),
})
export type AdminUpload = z.infer<typeof AdminUploadSchema>

export const AdminUploadFileSchema = z.object({
  url: z.string(),
  expires_in: z.number().int(),
  file_name: z.string(),
})
export type AdminUploadFile = z.infer<typeof AdminUploadFileSchema>

export const AdminUploadRejectRequestSchema = z.object({
  reason: z.string(),
})
export type AdminUploadRejectRequest = z.infer<typeof AdminUploadRejectRequestSchema>

export const AdminUploadReportSchema = z.object({
  report_id: z.string(),
  title: z.string().nullable().optional(),
  publication: z.enum(['draft', 'published']),
  hidden: z.boolean(),
  extractor: z.string().nullable().optional(),
  extraction_version: z.string().nullable().optional(),
  quality_score: z.number().nullable().optional(),
  page_count: z.number().int().nullable().optional(),
  pages_failed: z.array(z.number().int()).nullable().optional(),
  needs_review: z.boolean().optional(),
  created_at: z.string().nullable().optional(),
})
export type AdminUploadReport = z.infer<typeof AdminUploadReportSchema>

export const AdminUploadScannerSchema = z.object({
  pending: z.number().int(),
  scanning: z.number().int(),
  oldest_pending_at: z.string().nullable().optional(),
  oldest_pending_seconds: z.number().int().nullable().optional(),
  last_error: z.string().nullable().optional(),
  last_error_at: z.string().nullable().optional(),
})
export type AdminUploadScanner = z.infer<typeof AdminUploadScannerSchema>

export const AdminUploadTagsSchema = z.object({
  market: z.string().nullable().optional(),
  is_research: z.boolean(),
  confidence: z.number().nullable().optional(),
  source: z.string().nullable().optional(),
  report_date: z.string().nullable().optional(),
  report_type: z.string().nullable().optional(),
  language: z.string().nullable().optional(),
  stock_code: z.string().nullable().optional(),
  company_name: z.string().nullable().optional(),
  instrument_types: z.array(z.string()),
  stock_targets: z.array(z.string()),
  futures_targets: z.array(z.string()),
  relates_stock: z.boolean().nullable().optional(),
  relates_futures: z.boolean().nullable().optional(),
})
export type AdminUploadTags = z.infer<typeof AdminUploadTagsSchema>

export const AdminUploadTakeawaySchema = z.object({
  ordinal: z.number().int(),
  claim: z.string(),
  quote: z.string().nullable().optional(),
  quote_start: z.number().int().nullable().optional(),
  quote_end: z.number().int().nullable().optional(),
  anchor_method: z.string().nullable().optional(),
})
export type AdminUploadTakeaway = z.infer<typeof AdminUploadTakeawaySchema>

export const AnalyticsAuditCountSchema = z.object({
  action: z.string(),
  count: z.number().int(),
})
export type AnalyticsAuditCount = z.infer<typeof AnalyticsAuditCountSchema>

export const AnalyticsCellSchema = z.object({
  key: z.string(),
  label: z.string().nullable().optional(),
  value: z.number().int().nullable().optional(),
  users: z.number().int().nullable().optional(),
  suppressed: z.boolean(),
  suppression_reason: z.enum(['min_users', 'complementary']).nullable().optional(),
})
export type AnalyticsCell = z.infer<typeof AnalyticsCellSchema>

export const AnalyticsDailyPointSchema = z.object({
  day: z.string(),
  source: z.enum(['live', 'rollup']),
  has_data: z.boolean(),
  questions: z.number().int().nullable().optional(),
  askers: z.number().int().nullable().optional(),
  active_users: z.number().int().nullable().optional(),
  reading: z.number().int(),
  report_file: z.number().int(),
  search: z.number().int(),
  latency_p50_ms: z.number().nullable().optional(),
  latency_p95_ms: z.number().nullable().optional(),
  thinking_p50_ms: z.number().nullable().optional(),
  thinking_p95_ms: z.number().nullable().optional(),
})
export type AnalyticsDailyPoint = z.infer<typeof AnalyticsDailyPointSchema>

export const AnalyticsDistributionSchema = z.object({
  name: z.enum(['path', 'decided_by', 'llm_model', 'llm_error']),
  suppressible: z.boolean(),
  cells: z.array(AnalyticsCellSchema),
  suppressed_count: z.number().int(),
  complementary_count: z.number().int(),
})
export type AnalyticsDistribution = z.infer<typeof AnalyticsDistributionSchema>

export const AnalyticsLatencySchema = z.object({
  p50_ms: z.number().nullable().optional(),
  p95_ms: z.number().nullable().optional(),
  thinking_p50_ms: z.number().nullable().optional(),
  thinking_p95_ms: z.number().nullable().optional(),
  n: z.number().int(),
})
export type AnalyticsLatency = z.infer<typeof AnalyticsLatencySchema>

export const AnalyticsOpsWeekSchema = z.object({
  week_start: z.string(),
  partial: z.boolean(),
  uploads_received: z.number().int(),
  uploads_published: z.number().int(),
  uploads_rejected: z.number().int(),
  uploads_infected: z.number().int(),
  uploads_failed: z.number().int(),
  reviews: z.number().int(),
  qa_content_reads: z.number().int(),
})
export type AnalyticsOpsWeek = z.infer<typeof AnalyticsOpsWeekSchema>

export const AnalyticsOverviewTotalsSchema = z.object({
  questions: z.number().int(),
  stopped: z.number().int(),
  reading: z.number().int(),
  report_file: z.number().int(),
  search: z.number().int(),
  active_users_peak: z.number().int(),
  distinct_users_live: z.number().int().nullable().optional(),
  missing_days: z.number().int(),
})
export type AnalyticsOverviewTotals = z.infer<typeof AnalyticsOverviewTotalsSchema>

export const AnalyticsQualityWeekSchema = z.object({
  week_start: z.string(),
  partial: z.boolean(),
  questions: z.number().int(),
  checked_all: z.number().int(),
  judge_checked: z.number().int(),
  degraded: z.number().int(),
  below_min: z.number().int(),
  avg_score: z.number().nullable().optional(),
  score_n: z.number().int(),
  likes: z.number().int(),
  dislikes: z.number().int(),
})
export type AnalyticsQualityWeek = z.infer<typeof AnalyticsQualityWeekSchema>

export const AnalyticsSpanSchema = z.object({
  since: z.string(),
  until: z.string(),
  source: z.enum(['live', 'rollup']),
})
export type AnalyticsSpan = z.infer<typeof AnalyticsSpanSchema>

export const AnalyticsTopListSchema = z.object({
  cells: z.array(AnalyticsCellSchema),
  suppressed_count: z.number().int(),
  complementary_count: z.number().int(),
  truncated: z.boolean(),
})
export type AnalyticsTopList = z.infer<typeof AnalyticsTopListSchema>

export const AuditChainResponseSchema = z.object({
  ok: z.boolean(),
  total: z.number().int(),
  head_id: z.number().int().nullable(),
  head_hash: z.string().nullable(),
  broken_ids: z.array(z.number().int()),
})
export type AuditChainResponse = z.infer<typeof AuditChainResponseSchema>

export const AuditFindingSchema = z.object({
  key: z.string(),
  label: z.string(),
  severity: z.enum(['error', 'warn']),
  count: z.number().int(),
  detail: z.string(),
})
export type AuditFinding = z.infer<typeof AuditFindingSchema>

export const AuditItemSchema = z.object({
  id: z.number().int(),
  actor_user_id: z.string().nullable(),
  actor_username: z.string().nullable(),
  action: z.string(),
  target_type: z.string(),
  target_id: z.string().nullable(),
  detail: z.record(z.string(), z.unknown()),
  created_at: z.string().nullable(),
})
export type AuditItem = z.infer<typeof AuditItemSchema>

export const AuditResponseSchema = z.object({
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
  has_more: z.boolean(),
  next_offset: z.number().int().nullable(),
  items: z.array(AuditItemSchema),
})
export type AuditResponse = z.infer<typeof AuditResponseSchema>

export const BulkReportResultSchema = z.object({
  file_hash: z.string(),
  status: z.enum(['ok', 'skipped']),
  code: z.string().nullable().optional(),
  detail: z.string().nullable().optional(),
  hidden: z.boolean().nullable().optional(),
})
export type BulkReportResult = z.infer<typeof BulkReportResultSchema>

export const BulkUserResultItemSchema = z.object({
  user_id: z.string(),
  status: z.enum(['ok', 'unchanged', 'skipped']),
  code: z.string().nullable().optional(),
  detail: z.string().nullable().optional(),
  revoked_sessions: z.number().int().nullable().optional(),
})
export type BulkUserResultItem = z.infer<typeof BulkUserResultItemSchema>

export const BulkUsersRequestSchema = z.object({
  action: z.enum(['disable', 'enable', 'logout']),
  user_ids: z.array(z.string()),
})
export type BulkUsersRequest = z.infer<typeof BulkUsersRequestSchema>

export const BulkUsersResponseSchema = z.object({
  action: z.enum(['disable', 'enable', 'logout']),
  requested: z.number().int(),
  ok: z.number().int(),
  unchanged: z.number().int(),
  skipped: z.number().int(),
  results: z.array(BulkUserResultItemSchema),
})
export type BulkUsersResponse = z.infer<typeof BulkUsersResponseSchema>

export const BulkVisibilityRequestSchema = z.object({
  action: z.enum(['hide', 'restore']),
  file_hashes: z.array(z.string()),
  reason: z.string().nullable().optional(),
})
export type BulkVisibilityRequest = z.infer<typeof BulkVisibilityRequestSchema>

export const BulkVisibilityResponseSchema = z.object({
  action: z.enum(['hide', 'restore']),
  requested: z.number().int(),
  ok: z.number().int(),
  skipped: z.number().int(),
  results: z.array(BulkReportResultSchema),
})
export type BulkVisibilityResponse = z.infer<typeof BulkVisibilityResponseSchema>

export const ConfigFlagsSchema = z.object({
  ask_enable_web: z.boolean(),
  ask_rerank_enabled: z.boolean(),
  qa_agentic_enabled: z.boolean(),
  ask_faithfulness_enabled: z.boolean(),
  trusted_data_enabled: z.boolean(),
  skip_warmup: z.boolean(),
  dev_no_auth: z.boolean(),
})
export type ConfigFlags = z.infer<typeof ConfigFlagsSchema>

export const ConfigLimitsSchema = z.object({
  db_pool_size: z.number().int(),
  db_max_overflow: z.number().int(),
  db_pool_timeout_s: z.number(),
  db_statement_timeout_ms: z.number().int(),
  db_idle_tx_timeout_ms: z.number().int(),
  embed_max_concurrency: z.number().int(),
  ask_faithfulness_sample_rate: z.number(),
  ask_faithfulness_max_inflight: z.number().int(),
})
export type ConfigLimits = z.infer<typeof ConfigLimitsSchema>

export const CreateUserRequestSchema = z.object({
  username: z.string(),
  password: z.string(),
  role: z.enum(['admin', 'user']).optional(),
})
export type CreateUserRequest = z.infer<typeof CreateUserRequestSchema>

export const DbAuditSectionSchema = z.object({
  status: z.enum(['ok', 'warn', 'fail', 'unknown']),
  available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  finished_at: z.string().nullable().optional(),
  age_hours: z.number().nullable().optional(),
  stale: z.boolean(),
  exit_code: z.number().int().nullable().optional(),
  error: z.string().nullable().optional(),
  skipped: z.array(z.string()),
  findings: z.array(AuditFindingSchema),
})
export type DbAuditSection = z.infer<typeof DbAuditSectionSchema>

export const DbCheckSchema = z.object({
  ok: z.boolean(),
  latency_ms: z.number().nullable().optional(),
  server_version: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
})
export type DbCheck = z.infer<typeof DbCheckSchema>

export const DbPoolSectionSchema = z.object({
  pool_class: z.string().nullable().optional(),
  size: z.number().int().nullable().optional(),
  max_overflow: z.number().int().nullable().optional(),
  checked_out: z.number().int().nullable().optional(),
  checked_in: z.number().int().nullable().optional(),
  open_connections: z.number().int().nullable().optional(),
  error: z.string().nullable().optional(),
})
export type DbPoolSection = z.infer<typeof DbPoolSectionSchema>

export const DeletionItemSchema = z.object({
  id: z.number().int(),
  user_id: z.string(),
  username: z.string().nullable(),
  requested_by: z.string().nullable(),
  requested_by_username: z.string().nullable(),
  requested_at: z.string().nullable(),
  execute_after: z.string().nullable(),
  cancelled_at: z.string().nullable(),
  executed_at: z.string().nullable(),
  status: z.enum(['pending', 'cancelled', 'executed']),
})
export type DeletionItem = z.infer<typeof DeletionItemSchema>

export const DeletionListResponseSchema = z.object({
  items: z.array(DeletionItemSchema),
})
export type DeletionListResponse = z.infer<typeof DeletionListResponseSchema>

export const ElevateRequestSchema = z.object({
  password: z.string(),
  code: z.string().nullable().optional(),
})
export type ElevateRequest = z.infer<typeof ElevateRequestSchema>

export const ElevateResponseSchema = z.object({
  elevated_until: z.string(),
})
export type ElevateResponse = z.infer<typeof ElevateResponseSchema>

export const FreshnessFindingSchema = z.object({
  asset: z.string(),
  label: z.string(),
  state: z.enum(['fresh', 'stale', 'suppressed', 'disabled', 'upstream_stale']),
  latest: z.string().nullable().optional(),
  age_days: z.number().nullable().optional(),
  threshold_days: z.number().int(),
  detail: z.string(),
})
export type FreshnessFinding = z.infer<typeof FreshnessFindingSchema>

export const FreshnessSectionSchema = z.object({
  status: z.enum(['ok', 'warn', 'fail', 'unknown']),
  exit_code: z.number().int(),
  error: z.string().nullable().optional(),
  findings: z.array(FreshnessFindingSchema),
})
export type FreshnessSection = z.infer<typeof FreshnessSectionSchema>

export const FrontendBuildSchema = z.object({
  available: z.boolean().optional(),
  built_at: z.string().nullable().optional(),
  entry_assets: z.array(z.string()).optional(),
})
export type FrontendBuild = z.infer<typeof FrontendBuildSchema>

export const GateStatusSchema = z.object({
  name: z.string(),
  capacity: z.number().int(),
  in_use: z.number().int().nullable().optional(),
  waiting: z.number().int(),
  max_queue: z.number().int(),
})
export type GateStatus = z.infer<typeof GateStatusSchema>

export const GitInfoSchema = z.object({
  available: z.boolean().optional(),
  reason: z.string().nullable().optional(),
  commit_at_start: z.string().nullable().optional(),
  branch_at_start: z.string().nullable().optional(),
  commit_on_disk: z.string().nullable().optional(),
  branch_on_disk: z.string().nullable().optional(),
  restart_pending: z.boolean().nullable().optional(),
})
export type GitInfo = z.infer<typeof GitInfoSchema>

export const IncidentEventItemSchema = z.object({
  event_id: z.string(),
  occurred_at: z.string(),
  action: z.enum(['FIRING', 'REMINDER', 'ESCALATED', 'RESOLVED']),
  severity: z.enum(['CRITICAL', 'WARNING', 'RESOLVED']),
  reason: z.string(),
  status: z.string().nullable().optional(),
  summary: z.string().nullable().optional(),
  notified: z.boolean(),
  journal_excerpt: z.string().nullable().optional(),
  journal_truncated: z.boolean().optional(),
  journal_since: z.string().nullable().optional(),
  journal_until: z.string().nullable().optional(),
  journal_units: z.string().nullable().optional(),
})
export type IncidentEventItem = z.infer<typeof IncidentEventItemSchema>

export const IncidentItemSchema = z.object({
  incident_id: z.string(),
  host: z.string(),
  component: z.string(),
  kind: z.enum(['service', 'monitor_blind']),
  probe_unit: z.string().nullable().optional(),
  status: z.enum(['firing', 'resolved', 'lost']),
  severity: z.enum(['CRITICAL', 'WARNING']),
  reason: z.string(),
  summary: z.string().nullable().optional(),
  opened_at: z.string(),
  last_event_at: z.string(),
  resolved_at: z.string().nullable().optional(),
  duration_seconds: z.number().nullable().optional(),
  event_count: z.number().int(),
})
export type IncidentItem = z.infer<typeof IncidentItemSchema>

export const IncidentListResponseSchema = z.object({
  since: z.string(),
  until: z.string(),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
  has_more: z.boolean(),
  next_offset: z.number().int().nullable(),
  items: z.array(IncidentItemSchema),
})
export type IncidentListResponse = z.infer<typeof IncidentListResponseSchema>

export const JobItemSchema = z.object({
  host: z.string(),
  unit: z.string(),
  service: z.string().nullable().optional(),
  invocation_id: z.string(),
  state: z.enum(['running', 'finished', 'lost']),
  started_at: z.string(),
  finished_at: z.string().nullable().optional(),
  duration_seconds: z.number().nullable().optional(),
  result: z.string().nullable().optional(),
  exit_status: z.number().int().nullable().optional(),
  exec_main_code: z.enum(['exited', 'killed', 'dumped']).nullable().optional(),
  last_seen_at: z.string(),
})
export type JobItem = z.infer<typeof JobItemSchema>

export const JobListResponseSchema = z.object({
  since: z.string(),
  until: z.string(),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
  has_more: z.boolean(),
  next_offset: z.number().int().nullable(),
  items: z.array(JobItemSchema),
})
export type JobListResponse = z.infer<typeof JobListResponseSchema>

export const LlmCheckSchema = z.object({
  state: z.string(),
  key_configured: z.boolean().optional(),
  ask_uses_http: z.boolean().optional(),
  consecutive_failures: z.number().int().optional(),
  last_check_age_s: z.number().nullable().optional(),
  quota_latched: z.boolean().optional(),
  error: z.string().nullable().optional(),
})
export type LlmCheck = z.infer<typeof LlmCheckSchema>

export const LlmUsageDaySchema = z.object({
  calls: z.number().int(),
  failures: z.number().int(),
  prompt_hit_tokens: z.number().int(),
  prompt_miss_tokens: z.number().int(),
  completion_tokens: z.number().int(),
  reasoning_tokens: z.number().int(),
  calls_without_tokens: z.number().int(),
  total_ms: z.number().int(),
  cost: z.number().nullable().optional(),
  day: z.string(),
})
export type LlmUsageDay = z.infer<typeof LlmUsageDaySchema>

export const LlmUsageModelSchema = z.object({
  calls: z.number().int(),
  failures: z.number().int(),
  prompt_hit_tokens: z.number().int(),
  prompt_miss_tokens: z.number().int(),
  completion_tokens: z.number().int(),
  reasoning_tokens: z.number().int(),
  calls_without_tokens: z.number().int(),
  total_ms: z.number().int(),
  cost: z.number().nullable().optional(),
  model: z.string(),
})
export type LlmUsageModel = z.infer<typeof LlmUsageModelSchema>

export const LlmUsageRowSchema = z.object({
  calls: z.number().int(),
  failures: z.number().int(),
  prompt_hit_tokens: z.number().int(),
  prompt_miss_tokens: z.number().int(),
  completion_tokens: z.number().int(),
  reasoning_tokens: z.number().int(),
  calls_without_tokens: z.number().int(),
  total_ms: z.number().int(),
  cost: z.number().nullable().optional(),
  day: z.string(),
  task: z.string(),
  model: z.string(),
})
export type LlmUsageRow = z.infer<typeof LlmUsageRowSchema>

export const LlmUsageSourceSchema = z.object({
  exists: z.boolean(),
  size_bytes: z.number().int(),
  scanned_bytes: z.number().int(),
  truncated: z.boolean(),
  lines_scanned: z.number().int(),
  lines_invalid: z.number().int(),
  lines_in_range: z.number().int(),
  earliest_ts: z.string().nullable().optional(),
  latest_ts: z.string().nullable().optional(),
})
export type LlmUsageSource = z.infer<typeof LlmUsageSourceSchema>

export const LlmUsageTaskSchema = z.object({
  calls: z.number().int(),
  failures: z.number().int(),
  prompt_hit_tokens: z.number().int(),
  prompt_miss_tokens: z.number().int(),
  completion_tokens: z.number().int(),
  reasoning_tokens: z.number().int(),
  calls_without_tokens: z.number().int(),
  total_ms: z.number().int(),
  cost: z.number().nullable().optional(),
  task: z.string(),
})
export type LlmUsageTask = z.infer<typeof LlmUsageTaskSchema>

export const LlmUsageTotalsSchema = z.object({
  calls: z.number().int(),
  failures: z.number().int(),
  prompt_hit_tokens: z.number().int(),
  prompt_miss_tokens: z.number().int(),
  completion_tokens: z.number().int(),
  reasoning_tokens: z.number().int(),
  calls_without_tokens: z.number().int(),
  total_ms: z.number().int(),
  cost: z.number().nullable().optional(),
})
export type LlmUsageTotals = z.infer<typeof LlmUsageTotalsSchema>

export const LogoutResponseSchema = z.object({
  revoked: z.number().int(),
})
export type LogoutResponse = z.infer<typeof LogoutResponseSchema>

export const ModelsSectionSchema = z.object({
  embed_model: z.string().nullable().optional(),
  embed_loaded: z.boolean().optional(),
  rerank_loaded: z.boolean().optional(),
  rerank_load_failed: z.boolean().optional(),
  warmup: z.enum(['skipped', 'absent', 'running', 'done', 'failed', 'cancelled']).nullable().optional(),
  warmup_error: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
})
export type ModelsSection = z.infer<typeof ModelsSectionSchema>

export const ObservationItemSchema = z.object({
  observed_at: z.string(),
  host: z.string(),
  scope: z.enum(['host', 'container', 'service']),
  subject: z.string(),
  metric: z.string(),
  value: z.number().nullable().optional(),
  state: z.string().nullable().optional(),
  detail: z.record(z.string(), z.unknown()).nullable().optional(),
  sample_count: z.number().int().nullable().optional(),
  value_min: z.number().nullable().optional(),
  value_max: z.number().nullable().optional(),
  value_last: z.number().nullable().optional(),
  first_state: z.string().nullable().optional(),
  state_changes: z.number().int().nullable().optional(),
})
export type ObservationItem = z.infer<typeof ObservationItemSchema>

export const ObservationListResponseSchema = z.object({
  since: z.string(),
  until: z.string(),
  limit: z.number().int(),
  truncated: z.boolean(),
  resolution: z.enum(['raw', '5m', '1h']),
  items: z.array(ObservationItemSchema),
})
export type ObservationListResponse = z.infer<typeof ObservationListResponseSchema>

export const OpsActionResponseSchema = z.object({
  name: z.string(),
  kind: z.enum(['systemd', 'container']),
  tier: z.enum(['critical', 'important', 'supporting']),
  target: z.string(),
  timer: z.string().nullable().optional(),
  actions: z.array(z.enum(['status', 'logs', 'restart', 'run'])),
  group: z.string().nullable().optional(),
  description: z.string().optional(),
  action: z.enum(['restart', 'run']),
  state: z.enum(['scheduled', 'queued']),
  previous_invocation_id: z.string().nullable().optional(),
  previous_active_enter_at: z.string().nullable().optional(),
  previous_exec_main_start_at: z.string().nullable().optional(),
  execute_after_ms: z.number().int(),
  accepted_at: z.string(),
  checked_at: z.string(),
})
export type OpsActionResponse = z.infer<typeof OpsActionResponseSchema>

export const OpsAgentCheckSchema = z.object({
  ok: z.boolean(),
  latency_ms: z.number().nullable().optional(),
  services: z.number().int().nullable().optional(),
  error: z.string().nullable().optional(),
})
export type OpsAgentCheck = z.infer<typeof OpsAgentCheckSchema>

export const OpsContainerStateSchema = z.object({
  status: z.string().nullable().optional(),
  running: z.boolean().nullable().optional(),
  paused: z.boolean().nullable().optional(),
  restarting: z.boolean().nullable().optional(),
  oom_killed: z.boolean().nullable().optional(),
  dead: z.boolean().nullable().optional(),
  exit_code: z.number().int().nullable().optional(),
  error: z.string().nullable().optional(),
  started_at: z.string().nullable().optional(),
  finished_at: z.string().nullable().optional(),
  health: z.string().nullable().optional(),
  failing_streak: z.number().int().nullable().optional(),
  restart_count: z.number().int().nullable().optional(),
  image: z.string().nullable().optional(),
})
export type OpsContainerState = z.infer<typeof OpsContainerStateSchema>

export const OpsDependencyEdgeSchema = z.object({
  dependent: z.string(),
  dependency: z.string(),
  broken: z.boolean(),
})
export type OpsDependencyEdge = z.infer<typeof OpsDependencyEdgeSchema>

export const OpsDependencyNodeSchema = z.object({
  name: z.string(),
  kind: z.enum(['systemd', 'container', 'external']),
  tier: z.enum(['critical', 'important', 'supporting']),
  target: z.string().nullable().optional(),
  description: z.string().optional(),
  summary: z.enum(['running', 'idle', 'failed', 'transitioning', 'not_found', 'unknown']).nullable().optional(),
  health: z.enum(['ok', 'degraded', 'down', 'unknown']),
  health_reason: z.string(),
  probe: z.string().nullable().optional(),
  observed_at: z.string().nullable().optional(),
  depends_on: z.array(z.string()),
  dependents: z.array(z.string()),
  layer: z.number().int(),
  affected: z.boolean(),
  impacted_by: z.array(z.string()),
})
export type OpsDependencyNode = z.infer<typeof OpsDependencyNodeSchema>

export const OpsLogsResponseSchema = z.object({
  name: z.string(),
  kind: z.enum(['systemd', 'container']),
  tier: z.enum(['critical', 'important', 'supporting']),
  target: z.string(),
  since: z.string(),
  lines: z.number().int(),
  truncated: z.boolean(),
  entries: z.array(z.string()),
  checked_at: z.string(),
})
export type OpsLogsResponse = z.infer<typeof OpsLogsResponseSchema>

export const OpsSystemdStateSchema = z.object({
  load_state: z.string().nullable().optional(),
  active_state: z.string().nullable().optional(),
  sub_state: z.string().nullable().optional(),
  result: z.string().nullable().optional(),
  type: z.string().nullable().optional(),
  unit_file_state: z.string().nullable().optional(),
  exec_main_code: z.string().nullable().optional(),
  exec_main_status: z.number().int().nullable().optional(),
  main_pid: z.number().int().nullable().optional(),
  n_restarts: z.number().int().nullable().optional(),
  exec_main_start_at: z.string().nullable().optional(),
  exec_main_exit_at: z.string().nullable().optional(),
  active_enter_at: z.string().nullable().optional(),
  state_change_at: z.string().nullable().optional(),
  invocation_id: z.string().nullable().optional(),
})
export type OpsSystemdState = z.infer<typeof OpsSystemdStateSchema>

export const OpsTimerStateSchema = z.object({
  unit: z.string(),
  load_state: z.string().nullable().optional(),
  active_state: z.string().nullable().optional(),
  next_elapse_at: z.string().nullable().optional(),
  last_trigger_at: z.string().nullable().optional(),
})
export type OpsTimerState = z.infer<typeof OpsTimerStateSchema>

export const PasswordRequestSchema = z.object({
  password: z.string(),
})
export type PasswordRequest = z.infer<typeof PasswordRequestSchema>

export const PrivilegesRequestSchema = z.object({
  is_super: z.boolean().nullable().optional(),
  scopes: z.array(z.enum(['qa_content.read', 'ops.operate'])).nullable().optional(),
})
export type PrivilegesRequest = z.infer<typeof PrivilegesRequestSchema>

export const ReconcileIssueSchema = z.object({
  type: z.string(),
  ref: z.string(),
})
export type ReconcileIssue = z.infer<typeof ReconcileIssueSchema>

export const ReconcileStatsSchema = z.object({
  checked: z.number().int().optional(),
  errors: z.number().int().optional(),
  unkeyed: z.number().int().optional(),
  key_mismatch: z.number().int().optional(),
  missing: z.number().int().optional(),
  size_mismatch: z.number().int().optional(),
  sha_mismatch: z.number().int().optional(),
  sha_metadata_missing: z.number().int().optional(),
  orphans: z.number().int().optional(),
})
export type ReconcileStats = z.infer<typeof ReconcileStatsSchema>

export const RegressionBaselineSchema = z.object({
  captured_at: z.string().nullable().optional(),
  corpus_cutoff: z.string().nullable().optional(),
  corpus_reports: z.number().int(),
  simulated_as_of: z.boolean(),
  k: z.number().int(),
  dense_scan: z.number().int(),
  dataset_sha256: z.string().nullable().optional(),
})
export type RegressionBaseline = z.infer<typeof RegressionBaselineSchema>

export const RegressionReportRefSchema = z.object({
  file_hash: z.string(),
  label: z.string().nullable().optional(),
})
export type RegressionReportRef = z.infer<typeof RegressionReportRefSchema>

export const RegressionSummarySchema = z.object({
  verdict: z.enum(['ok', 'degraded', 'incomparable']),
  questions: z.number().int(),
  comparable: z.number().int(),
  mean_report_recall: z.number().nullable().optional(),
  mean_raw_report_recall: z.number().nullable().optional(),
  mean_chunk_recall: z.number().nullable().optional(),
  mean_rbo: z.number().nullable().optional(),
  degraded_questions: z.number().int(),
  hidden_reports: z.number().int(),
  removed_reports: z.number().int(),
  excluded_new_reports: z.number().int(),
  lex_truncated_questions: z.number().int().optional(),
})
export type RegressionSummary = z.infer<typeof RegressionSummarySchema>

export const RegressionThresholdsSchema = z.object({
  min_mean_recall: z.number(),
  min_question_recall: z.number(),
  max_degraded_questions: z.number().int(),
})
export type RegressionThresholds = z.infer<typeof RegressionThresholdsSchema>

export const ReportVisibilityRequestSchema = z.object({
  hidden: z.boolean(),
  reason: z.string().nullable().optional(),
})
export type ReportVisibilityRequest = z.infer<typeof ReportVisibilityRequestSchema>

export const ReportVisibilityResponseSchema = z.object({
  file_hash: z.string(),
  hidden: z.boolean(),
  reason: z.string().nullable().optional(),
  updated_by: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
})
export type ReportVisibilityResponse = z.infer<typeof ReportVisibilityResponseSchema>

export const SchemaDailyCheckSchema = z.object({
  available: z.boolean().optional(),
  unavailable_reason: z.string().nullable().optional(),
  checked_at: z.string().nullable().optional(),
  age_hours: z.number().nullable().optional(),
  stale: z.boolean().optional(),
  mode: z.string().nullable().optional(),
  exit_code: z.number().int().nullable().optional(),
  alert: z.boolean().nullable().optional(),
  problems: z.array(z.string()).optional(),
  message: z.string().nullable().optional(),
  version_status: z.string().nullable().optional(),
  drift_status: z.string().nullable().optional(),
  drift_count: z.number().int().nullable().optional(),
  error: z.string().nullable().optional(),
})
export type SchemaDailyCheck = z.infer<typeof SchemaDailyCheckSchema>

export const SchemaSectionSchema = z.object({
  status: z.enum(['ok', 'behind', 'ahead', 'unversioned', 'ambiguous', 'error']),
  db_revisions: z.array(z.string()),
  code_heads: z.array(z.string()),
  pending: z.array(z.string()),
  error: z.string().nullable().optional(),
  daily_check: SchemaDailyCheckSchema,
})
export type SchemaSection = z.infer<typeof SchemaSectionSchema>

export const SecretsPresentSchema = z.object({
  deepseek_api_key: z.boolean(),
  session_secret: z.boolean(),
  edge_secret: z.boolean(),
  r2_credentials: z.boolean(),
  alert_webhook: z.boolean(),
})
export type SecretsPresent = z.infer<typeof SecretsPresentSchema>

export const StorageCheckSchema = z.object({
  state: z.string(),
  consecutive_failures: z.number().int().optional(),
  last_probe_age_s: z.number().nullable().optional(),
  last_probe_ok: z.boolean().nullable().optional(),
  error: z.string().nullable().optional(),
})
export type StorageCheck = z.infer<typeof StorageCheckSchema>

export const TimezoneInfoSchema = z.object({
  tz_env: z.string().nullable().optional(),
  name: z.string().optional(),
  utc_offset: z.string().optional(),
})
export type TimezoneInfo = z.infer<typeof TimezoneInfoSchema>

export const UpdateUserRequestSchema = z.object({
  role: z.enum(['admin', 'user']).nullable().optional(),
  enabled: z.boolean().nullable().optional(),
})
export type UpdateUserRequest = z.infer<typeof UpdateUserRequestSchema>

export const UserItemSchema = z.object({
  id: z.string(),
  username: z.string(),
  role: z.enum(['admin', 'user']),
  enabled: z.boolean(),
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
  password_changed_at: z.string().nullable().optional(),
  last_login_at: z.string().nullable().optional(),
  last_seen_at: z.string().nullable().optional(),
  active_sessions: z.number().int().optional(),
  is_super: z.boolean().optional(),
  scopes: z.array(z.enum(['qa_content.read', 'ops.operate'])).optional(),
  totp_enabled: z.boolean().optional(),
  deletion_execute_after: z.string().nullable().optional(),
})
export type UserItem = z.infer<typeof UserItemSchema>

export const UserListResponseSchema = z.object({
  items: z.array(UserItemSchema),
})
export type UserListResponse = z.infer<typeof UserListResponseSchema>

export const VersionsSectionSchema = z.object({
  git: GitInfoSchema.nullable().optional(),
  frontend: FrontendBuildSchema.nullable().optional(),
  python: z.string().nullable().optional(),
  platform: z.string().nullable().optional(),
  packages: z.record(z.string(), z.unknown()).optional(),
  error: z.string().nullable().optional(),
})
export type VersionsSection = z.infer<typeof VersionsSectionSchema>

export const AdminUploadDetailSchema = z.object({
  upload_id: z.string(),
  file_hash: z.string(),
  original_name: z.string(),
  size_bytes: z.number().int(),
  client_mtime: z.string().nullable().optional(),
  uploaded_by: z.string().nullable().optional(),
  uploaded_at: z.string(),
  state: z.enum(['quarantined', 'scanning', 'clean', 'infected', 'blocked', 'processing', 'draft', 'failed', 'duplicate', 'published', 'rejected']),
  state_changed_at: z.string(),
  scan_attempts: z.number().int().optional(),
  scan_engine: z.string().nullable().optional(),
  scan_signature: z.string().nullable().optional(),
  scanned_at: z.string().nullable().optional(),
  scan_last_error: z.string().nullable().optional(),
  process_attempts: z.number().int().optional(),
  failure_kind: z.string().nullable().optional(),
  failure_detail: z.string().nullable().optional(),
  processed_at: z.string().nullable().optional(),
  decided_by: z.string().nullable().optional(),
  decided_at: z.string().nullable().optional(),
  decision_reason: z.string().nullable().optional(),
  purge_after: z.string().nullable().optional(),
  purged_at: z.string().nullable().optional(),
  report: AdminUploadReportSchema.nullable().optional(),
})
export type AdminUploadDetail = z.infer<typeof AdminUploadDetailSchema>

export const AdminUploadListResponseSchema = z.object({
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
  has_more: z.boolean(),
  next_offset: z.number().int().nullable(),
  items: z.array(AdminUploadSchema),
  scanner: AdminUploadScannerSchema,
})
export type AdminUploadListResponse = z.infer<typeof AdminUploadListResponseSchema>

export const AdminUploadPreviewSchema = z.object({
  upload: AdminUploadSchema,
  report_id: z.string(),
  file_name: z.string(),
  publication: z.enum(['draft', 'published']),
  hidden: z.boolean(),
  title: z.string().nullable().optional(),
  title_original: z.string().nullable().optional(),
  title_state: z.enum(['ready', 'pending']),
  summary: z.string().nullable().optional(),
  summary_state: z.enum(['ready', 'pending']),
  tags: AdminUploadTagsSchema,
  text: z.string().nullable().optional(),
  text_state: z.enum(['ready', 'missing']),
  text_chars: z.number().int(),
  text_truncated: z.boolean(),
  text_sha256: z.string().nullable().optional(),
  takeaways_state: z.enum(['ready', 'pending', 'none']),
  takeaways: z.array(AdminUploadTakeawaySchema),
})
export type AdminUploadPreview = z.infer<typeof AdminUploadPreviewSchema>

export const AnalyticsRangeSchema = z.object({
  since: z.string(),
  until: z.string(),
  today: z.string(),
  live_since: z.string(),
  timezone: z.string(),
  min_users: z.number().int(),
  spans: z.array(AnalyticsSpanSchema),
})
export type AnalyticsRange = z.infer<typeof AnalyticsRangeSchema>

export const AnalyticsRoutesResponseSchema = z.object({
  range: AnalyticsRangeSchema,
  questions: z.number().int(),
  stopped: z.number().int(),
  llm_truncated: z.number().int(),
  invalid_citation_rows: z.number().int(),
  invalid_citations: z.number().int(),
  distributions: z.array(AnalyticsDistributionSchema),
})
export type AnalyticsRoutesResponse = z.infer<typeof AnalyticsRoutesResponseSchema>

export const AnalyticsTopResponseSchema = z.object({
  range: AnalyticsRangeSchema,
  limit: z.number().int(),
  targets: AnalyticsTopListSchema,
  reports: AnalyticsTopListSchema,
  markets: AnalyticsTopListSchema,
  reading: AnalyticsTopListSchema,
  report_file: AnalyticsTopListSchema,
  search_markets: AnalyticsTopListSchema,
})
export type AnalyticsTopResponse = z.infer<typeof AnalyticsTopResponseSchema>

export const ConfigSectionSchema = z.object({
  db_target: z.string().nullable().optional(),
  object_storage_mode: z.string().nullable().optional(),
  llm_provider: z.string().nullable().optional(),
  models: z.record(z.string(), z.unknown()).optional(),
  extractor: z.string().nullable().optional(),
  log_level: z.string().nullable().optional(),
  ops_agent_environment: z.string().nullable().optional(),
  ops_agent_socket: z.string().nullable().optional(),
  flags: ConfigFlagsSchema.nullable().optional(),
  secrets_present: SecretsPresentSchema.nullable().optional(),
  limits: ConfigLimitsSchema.nullable().optional(),
  error: z.string().nullable().optional(),
})
export type ConfigSection = z.infer<typeof ConfigSectionSchema>

export const DiagnosticsChecksSchema = z.object({
  db: DbCheckSchema,
  storage: StorageCheckSchema,
  llm: LlmCheckSchema,
  ops_agent: OpsAgentCheckSchema,
})
export type DiagnosticsChecks = z.infer<typeof DiagnosticsChecksSchema>

export const IncidentDetailSchema = z.object({
  incident_id: z.string(),
  host: z.string(),
  component: z.string(),
  kind: z.enum(['service', 'monitor_blind']),
  probe_unit: z.string().nullable().optional(),
  status: z.enum(['firing', 'resolved', 'lost']),
  severity: z.enum(['CRITICAL', 'WARNING']),
  reason: z.string(),
  summary: z.string().nullable().optional(),
  opened_at: z.string(),
  last_event_at: z.string(),
  resolved_at: z.string().nullable().optional(),
  duration_seconds: z.number().nullable().optional(),
  event_count: z.number().int(),
  events: z.array(IncidentEventItemSchema),
  events_truncated: z.boolean(),
})
export type IncidentDetail = z.infer<typeof IncidentDetailSchema>

export const LlmUsageResponseSchema = z.object({
  since: z.string(),
  until: z.string(),
  timezone: z.string(),
  source: LlmUsageSourceSchema,
  totals: LlmUsageTotalsSchema,
  by_day: z.array(LlmUsageDaySchema),
  by_task: z.array(LlmUsageTaskSchema),
  by_model: z.array(LlmUsageModelSchema),
  rows: z.array(LlmUsageRowSchema),
  rows_truncated: z.boolean(),
  cost_available: z.boolean(),
})
export type LlmUsageResponse = z.infer<typeof LlmUsageResponseSchema>

export const OpsDependencyGraphSchema = z.object({
  environment: z.string(),
  host: z.string(),
  checked_at: z.string(),
  nodes: z.array(OpsDependencyNodeSchema),
  edges: z.array(OpsDependencyEdgeSchema),
  down: z.array(z.string()),
  root_causes: z.array(z.string()),
  affected: z.array(z.string()),
})
export type OpsDependencyGraph = z.infer<typeof OpsDependencyGraphSchema>

export const OpsServiceDetailSchema = z.object({
  name: z.string(),
  kind: z.enum(['systemd', 'container']),
  tier: z.enum(['critical', 'important', 'supporting']),
  target: z.string(),
  timer: z.string().nullable().optional(),
  actions: z.array(z.enum(['status', 'logs', 'restart', 'run'])),
  group: z.string().nullable().optional(),
  description: z.string().optional(),
  summary: z.enum(['running', 'idle', 'failed', 'transitioning', 'not_found', 'unknown']),
  error: z.string().nullable().optional(),
  systemd: OpsSystemdStateSchema.nullable().optional(),
  container: OpsContainerStateSchema.nullable().optional(),
  timer_state: OpsTimerStateSchema.nullable().optional(),
  checked_at: z.string(),
})
export type OpsServiceDetail = z.infer<typeof OpsServiceDetailSchema>

export const OpsServiceStatusSchema = z.object({
  name: z.string(),
  kind: z.enum(['systemd', 'container']),
  tier: z.enum(['critical', 'important', 'supporting']),
  target: z.string(),
  timer: z.string().nullable().optional(),
  actions: z.array(z.enum(['status', 'logs', 'restart', 'run'])),
  group: z.string().nullable().optional(),
  description: z.string().optional(),
  summary: z.enum(['running', 'idle', 'failed', 'transitioning', 'not_found', 'unknown']),
  error: z.string().nullable().optional(),
  systemd: OpsSystemdStateSchema.nullable().optional(),
  container: OpsContainerStateSchema.nullable().optional(),
  timer_state: OpsTimerStateSchema.nullable().optional(),
})
export type OpsServiceStatus = z.infer<typeof OpsServiceStatusSchema>

export const R2ReconcileSectionSchema = z.object({
  status: z.enum(['ok', 'warn', 'fail', 'unknown']),
  available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  finished_at: z.string().nullable().optional(),
  age_hours: z.number().nullable().optional(),
  stale: z.boolean(),
  exit_code: z.number().int().nullable().optional(),
  mode: z.enum(['local', 'r2']).nullable().optional(),
  dry_run: z.boolean().nullable().optional(),
  limit: z.number().int().nullable().optional(),
  orphan_scan: z.enum(['done', 'skipped', 'error']).nullable().optional(),
  stats: ReconcileStatsSchema.nullable().optional(),
  issues: z.array(ReconcileIssueSchema),
  issues_total: z.number().int(),
})
export type R2ReconcileSection = z.infer<typeof R2ReconcileSectionSchema>

export const RegressionQuestionSchema = z.object({
  id: z.string(),
  question: z.string(),
  comparable: z.boolean(),
  degraded: z.boolean(),
  lex_truncated: z.boolean().optional(),
  report_recall: z.number().nullable().optional(),
  raw_report_recall: z.number().nullable().optional(),
  chunk_recall: z.number().nullable().optional(),
  rbo: z.number().nullable().optional(),
  baseline_reports: z.number().int(),
  eligible_reports: z.number().int(),
  current_reports: z.number().int(),
  hidden_reports: z.number().int(),
  removed_reports: z.number().int(),
  excluded_new_reports: z.number().int(),
  lost_total: z.number().int(),
  gained_total: z.number().int(),
  lost: z.array(RegressionReportRefSchema),
  gained: z.array(RegressionReportRefSchema),
})
export type RegressionQuestion = z.infer<typeof RegressionQuestionSchema>

export const RuntimeSectionSchema = z.object({
  pid: z.number().int().nullable().optional(),
  hostname: z.string().nullable().optional(),
  started_at: z.string().nullable().optional(),
  uptime_s: z.number().nullable().optional(),
  rss_bytes: z.number().int().nullable().optional(),
  peak_rss_bytes: z.number().int().nullable().optional(),
  threads: z.number().int().nullable().optional(),
  timezone: TimezoneInfoSchema.nullable().optional(),
  python_executable: z.string().nullable().optional(),
  error: z.string().nullable().optional(),
})
export type RuntimeSection = z.infer<typeof RuntimeSectionSchema>

export const AnalyticsOperationsResponseSchema = z.object({
  range: AnalyticsRangeSchema,
  weeks: z.array(AnalyticsOpsWeekSchema),
  audit_actions: z.array(AnalyticsAuditCountSchema),
})
export type AnalyticsOperationsResponse = z.infer<typeof AnalyticsOperationsResponseSchema>

export const AnalyticsOverviewResponseSchema = z.object({
  range: AnalyticsRangeSchema,
  totals: AnalyticsOverviewTotalsSchema,
  latency: AnalyticsLatencySchema,
  daily: z.array(AnalyticsDailyPointSchema),
})
export type AnalyticsOverviewResponse = z.infer<typeof AnalyticsOverviewResponseSchema>

export const AnalyticsQualityResponseSchema = z.object({
  range: AnalyticsRangeSchema,
  judge_model: z.string(),
  faithfulness_min: z.number(),
  other_judge_checked: z.number().int(),
  weeks: z.array(AnalyticsQualityWeekSchema),
})
export type AnalyticsQualityResponse = z.infer<typeof AnalyticsQualityResponseSchema>

export const DataHealthResponseSchema = z.object({
  generated_at: z.string(),
  overall: z.enum(['ok', 'warn', 'fail', 'unknown']),
  freshness: FreshnessSectionSchema,
  db_audit: DbAuditSectionSchema,
  r2_reconcile: R2ReconcileSectionSchema,
})
export type DataHealthResponse = z.infer<typeof DataHealthResponseSchema>

export const DiagnosticsResponseSchema = z.object({
  generated_at: z.string(),
  cache_ttl_s: z.number().int(),
  versions: VersionsSectionSchema,
  schema_info: SchemaSectionSchema,
  runtime: RuntimeSectionSchema,
  config: ConfigSectionSchema,
  models: ModelsSectionSchema,
  db_pool: DbPoolSectionSchema,
  gates: z.array(GateStatusSchema),
  gates_error: z.string().nullable().optional(),
  checks: DiagnosticsChecksSchema,
})
export type DiagnosticsResponse = z.infer<typeof DiagnosticsResponseSchema>

export const OpsServiceListResponseSchema = z.object({
  environment: z.string(),
  host: z.string(),
  checked_at: z.string(),
  items: z.array(OpsServiceStatusSchema),
})
export type OpsServiceListResponse = z.infer<typeof OpsServiceListResponseSchema>

export const RegressionComparisonSchema = z.object({
  finished_at: z.string(),
  duration_s: z.number().nullable().optional(),
  baseline: RegressionBaselineSchema.nullable().optional(),
  dense_scan: z.number().int().nullable().optional(),
  params_changed: z.boolean(),
  thresholds: RegressionThresholdsSchema.nullable().optional(),
  summary: RegressionSummarySchema.nullable().optional(),
  questions: z.array(RegressionQuestionSchema),
})
export type RegressionComparison = z.infer<typeof RegressionComparisonSchema>

export const RetrievalRegressionResponseSchema = z.object({
  status: z.enum(['ok', 'warn', 'fail', 'unknown']),
  available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  finished_at: z.string().nullable().optional(),
  age_hours: z.number().nullable().optional(),
  stale: z.boolean(),
  exit_code: z.number().int().nullable().optional(),
  outcome: z.enum(['ok', 'degraded', 'skipped', 'error']).nullable().optional(),
  reason: z.enum(['db_unavailable', 'low_memory', 'sync_running', 'no_baseline', 'baseline_invalid', 'incomparable', 'dataset_invalid', 'embed_failed', 'query_failed', 'unexpected']).nullable().optional(),
  message: z.string().nullable().optional(),
  comparison: RegressionComparisonSchema.nullable().optional(),
})
export type RetrievalRegressionResponse = z.infer<typeof RetrievalRegressionResponseSchema>

function qs(query: Record<string, string | number | boolean | null | undefined>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null) params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

export const adminApi = {
  /** GET /api/admin/analytics/operations — Get Analytics Operations */
  getAnalyticsOperations: (query: { since?: string | null; until?: string | null } = {}) => requestJSON(`/api/admin/analytics/operations${qs(query)}`, AnalyticsOperationsResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/analytics/overview — Get Analytics Overview */
  getAnalyticsOverview: (query: { since?: string | null; until?: string | null } = {}) => requestJSON(`/api/admin/analytics/overview${qs(query)}`, AnalyticsOverviewResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/analytics/quality — Get Analytics Quality */
  getAnalyticsQuality: (query: { since?: string | null; until?: string | null } = {}) => requestJSON(`/api/admin/analytics/quality${qs(query)}`, AnalyticsQualityResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/analytics/routes — Get Analytics Routes */
  getAnalyticsRoutes: (query: { since?: string | null; until?: string | null } = {}) => requestJSON(`/api/admin/analytics/routes${qs(query)}`, AnalyticsRoutesResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/analytics/top — Get Analytics Top */
  getAnalyticsTop: (query: { since?: string | null; until?: string | null; limit?: number } = {}) => requestJSON(`/api/admin/analytics/top${qs(query)}`, AnalyticsTopResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/audit — Audit Log */
  auditLog: (query: { limit?: number; offset?: number } = {}) => requestJSON(`/api/admin/audit${qs(query)}`, AuditResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/audit/verify — Verify Audit Chain */
  verifyAuditChain: () => requestJSON('/api/admin/audit/verify', AuditChainResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/data-health — Get Data Health */
  getDataHealth: () => requestJSON('/api/admin/data-health', DataHealthResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/deletions — List Deletions */
  listDeletions: (query: { status?: 'pending' | 'all' } = {}) => requestJSON(`/api/admin/deletions${qs(query)}`, DeletionListResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/diagnostics — Get Diagnostics */
  getDiagnostics: () => requestJSON('/api/admin/diagnostics', DiagnosticsResponseSchema, { cache: 'no-store' }),
  /** POST /api/admin/elevate — Elevate */
  elevate: (body: z.input<typeof ElevateRequestSchema>) => requestJSON('/api/admin/elevate', ElevateResponseSchema, jsonBody('POST', body)),
  /** GET /api/admin/incidents — List Incidents */
  listIncidents: (query: { status?: 'firing' | 'resolved' | 'lost' | null; component?: string | null; since?: string | null; until?: string | null; limit?: number; offset?: number } = {}) => requestJSON(`/api/admin/incidents${qs(query)}`, IncidentListResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/incidents/{incident_id} — Get Incident */
  getIncident: (incidentId: string) => requestJSON(`/api/admin/incidents/${encodeURIComponent(incidentId)}`, IncidentDetailSchema, { cache: 'no-store' }),
  /** GET /api/admin/jobs — List Jobs */
  listJobs: (query: { service?: string | null; unit?: string | null; state?: 'running' | 'finished' | 'lost' | null; result?: string | null; since?: string | null; until?: string | null; limit?: number; offset?: number } = {}) => requestJSON(`/api/admin/jobs${qs(query)}`, JobListResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/llm-usage — Get Llm Usage */
  getLlmUsage: (query: { since?: string | null; until?: string | null } = {}) => requestJSON(`/api/admin/llm-usage${qs(query)}`, LlmUsageResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/observations — List Observations */
  listObservations: (query: { scope?: 'host' | 'container' | 'service' | null; subject?: string | null; metric?: string | null; since?: string | null; until?: string | null; limit?: number } = {}) => requestJSON(`/api/admin/observations${qs(query)}`, ObservationListResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/ops/dependencies — Get Ops Dependencies */
  getOpsDependencies: () => requestJSON('/api/admin/ops/dependencies', OpsDependencyGraphSchema, { cache: 'no-store' }),
  /** GET /api/admin/ops/services — List Ops Services */
  listOpsServices: () => requestJSON('/api/admin/ops/services', OpsServiceListResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/ops/services/{name} — Get Ops Service */
  getOpsService: (name: string) => requestJSON(`/api/admin/ops/services/${encodeURIComponent(name)}`, OpsServiceDetailSchema, { cache: 'no-store' }),
  /** GET /api/admin/ops/services/{name}/logs — Get Ops Service Logs */
  getOpsServiceLogs: (name: string, query: { since?: string; lines?: number } = {}) => requestJSON(`/api/admin/ops/services/${encodeURIComponent(name)}/logs${qs(query)}`, OpsLogsResponseSchema, { cache: 'no-store' }),
  /** POST /api/admin/ops/services/{name}/restart — Restart Ops Service */
  restartOpsService: (name: string) => requestJSON(`/api/admin/ops/services/${encodeURIComponent(name)}/restart`, OpsActionResponseSchema, jsonBody('POST')),
  /** POST /api/admin/ops/services/{name}/run — Run Ops Service */
  runOpsService: (name: string) => requestJSON(`/api/admin/ops/services/${encodeURIComponent(name)}/run`, OpsActionResponseSchema, jsonBody('POST')),
  /** GET /api/admin/reports — List Reports */
  listReports: (query: { q?: string | null; hidden?: boolean | null; publication?: 'draft' | 'published' | null; limit?: number; offset?: number } = {}) => requestJSON(`/api/admin/reports${qs(query)}`, AdminReportListResponseSchema, { cache: 'no-store' }),
  /** POST /api/admin/reports/bulk-visibility — Bulk Set Report Visibility */
  bulkSetReportVisibility: (body: z.input<typeof BulkVisibilityRequestSchema>) => requestJSON('/api/admin/reports/bulk-visibility', BulkVisibilityResponseSchema, jsonBody('POST', body)),
  /** PUT /api/admin/reports/{file_hash}/visibility — Set Report Visibility */
  setReportVisibility: (fileHash: string, body: z.input<typeof ReportVisibilityRequestSchema>) => requestJSON(`/api/admin/reports/${encodeURIComponent(fileHash)}/visibility`, ReportVisibilityResponseSchema, jsonBody('PUT', body)),
  /** GET /api/admin/retrieval-regression — Get Retrieval Regression */
  getRetrievalRegression: () => requestJSON('/api/admin/retrieval-regression', RetrievalRegressionResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/uploads — List Uploads */
  listUploads: (query: { state?: 'quarantined' | 'scanning' | 'clean' | 'infected' | 'blocked' | 'processing' | 'draft' | 'failed' | 'duplicate' | 'published' | 'rejected' | null; limit?: number; offset?: number } = {}) => requestJSON(`/api/admin/uploads${qs(query)}`, AdminUploadListResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/uploads/{upload_id} — Get Upload */
  getUpload: (uploadId: string) => requestJSON(`/api/admin/uploads/${encodeURIComponent(uploadId)}`, AdminUploadDetailSchema, { cache: 'no-store' }),
  /** GET /api/admin/uploads/{upload_id}/file — Get Upload File */
  getUploadFile: (uploadId: string) => requestJSON(`/api/admin/uploads/${encodeURIComponent(uploadId)}/file`, AdminUploadFileSchema, { cache: 'no-store' }),
  /** GET /api/admin/uploads/{upload_id}/preview — Preview Upload */
  previewUpload: (uploadId: string) => requestJSON(`/api/admin/uploads/${encodeURIComponent(uploadId)}/preview`, AdminUploadPreviewSchema, { cache: 'no-store' }),
  /** POST /api/admin/uploads/{upload_id}/publish — Publish Upload */
  publishUpload: (uploadId: string) => requestJSON(`/api/admin/uploads/${encodeURIComponent(uploadId)}/publish`, AdminUploadSchema, jsonBody('POST')),
  /** POST /api/admin/uploads/{upload_id}/reject — Reject Upload */
  rejectUpload: (uploadId: string, body: z.input<typeof AdminUploadRejectRequestSchema>) => requestJSON(`/api/admin/uploads/${encodeURIComponent(uploadId)}/reject`, AdminUploadSchema, jsonBody('POST', body)),
  /** POST /api/admin/uploads/{upload_id}/retry — Retry Upload */
  retryUpload: (uploadId: string) => requestJSON(`/api/admin/uploads/${encodeURIComponent(uploadId)}/retry`, AdminUploadSchema, jsonBody('POST')),
  /** POST /api/admin/uploads/{upload_id}/unreject — Unreject Upload */
  unrejectUpload: (uploadId: string) => requestJSON(`/api/admin/uploads/${encodeURIComponent(uploadId)}/unreject`, AdminUploadSchema, jsonBody('POST')),
  /** GET /api/admin/users — List Users */
  listUsers: () => requestJSON('/api/admin/users', UserListResponseSchema, { cache: 'no-store' }),
  /** POST /api/admin/users — Create User */
  createUser: (body: z.input<typeof CreateUserRequestSchema>) => requestJSON('/api/admin/users', UserItemSchema, jsonBody('POST', body)),
  /** POST /api/admin/users/bulk — Bulk User Action */
  bulkUserAction: (body: z.input<typeof BulkUsersRequestSchema>) => requestJSON('/api/admin/users/bulk', BulkUsersResponseSchema, jsonBody('POST', body)),
  /** PATCH /api/admin/users/{user_id} — Update User */
  updateUser: (userId: string, body: z.input<typeof UpdateUserRequestSchema>) => requestJSON(`/api/admin/users/${encodeURIComponent(userId)}`, UserItemSchema, jsonBody('PATCH', body)),
  /** POST /api/admin/users/{user_id}/deletion — Request Deletion */
  requestDeletion: (userId: string) => requestJSON(`/api/admin/users/${encodeURIComponent(userId)}/deletion`, DeletionItemSchema, jsonBody('POST')),
  /** POST /api/admin/users/{user_id}/deletion/cancel — Cancel Deletion */
  cancelDeletion: (userId: string) => requestJSON(`/api/admin/users/${encodeURIComponent(userId)}/deletion/cancel`, DeletionItemSchema, jsonBody('POST')),
  /** POST /api/admin/users/{user_id}/logout — Force Logout */
  forceLogout: (userId: string) => requestJSON(`/api/admin/users/${encodeURIComponent(userId)}/logout`, LogoutResponseSchema, jsonBody('POST')),
  /** POST /api/admin/users/{user_id}/password — Reset Password */
  resetPassword: (userId: string, body: z.input<typeof PasswordRequestSchema>) => requestJSON(`/api/admin/users/${encodeURIComponent(userId)}/password`, UserItemSchema, jsonBody('POST', body)),
  /** PUT /api/admin/users/{user_id}/privileges — Set Privileges */
  setPrivileges: (userId: string, body: z.input<typeof PrivilegesRequestSchema>) => requestJSON(`/api/admin/users/${encodeURIComponent(userId)}/privileges`, UserItemSchema, jsonBody('PUT', body)),
  /** POST /api/admin/users/{user_id}/totp/reset — Reset Totp */
  resetTotp: (userId: string) => requestJSON(`/api/admin/users/${encodeURIComponent(userId)}/totp/reset`, UserItemSchema, jsonBody('POST')),
}

/** raw body 上傳端點（不是 JSON）：前端自己送檔（XHR 才有上傳進度），回應以 `response` 解析。 */
export const adminRawUploads = {
  /** POST /api/admin/uploads — Create Upload（raw body application/pdf） */
  createUpload: {
    method: 'POST',
    url: (query: { filename: string; last_modified?: number | null }) => `/api/admin/uploads${qs(query)}`,
    contentType: 'application/pdf',
    response: AdminUploadSchema,
  },
}

/** CSV 下載端點的網址（`text/csv`，不是 JSON）。 */
export const adminCsvUrls = {
  /** GET /api/admin/export/audit.csv — Export Audit（下載網址） */
  exportAudit: (query: { limit?: number } = {}) => `/api/admin/export/audit.csv${qs(query)}`,
  /** GET /api/admin/export/incidents.csv — Export Incidents（下載網址） */
  exportIncidents: (query: { status?: 'firing' | 'resolved' | 'lost' | null; component?: string | null; since?: string | null; until?: string | null; limit?: number } = {}) => `/api/admin/export/incidents.csv${qs(query)}`,
  /** GET /api/admin/export/jobs.csv — Export Jobs（下載網址） */
  exportJobs: (query: { service?: string | null; unit?: string | null; state?: 'running' | 'finished' | 'lost' | null; result?: string | null; since?: string | null; until?: string | null; limit?: number } = {}) => `/api/admin/export/jobs.csv${qs(query)}`,
  /** GET /api/admin/export/reports.csv — Export Reports（下載網址） */
  exportReports: (query: { q?: string | null; hidden?: boolean | null; publication?: 'draft' | 'published' | null; limit?: number } = {}) => `/api/admin/export/reports.csv${qs(query)}`,
  /** GET /api/admin/export/users.csv — Export Users（下載網址） */
  exportUsers: (query: { limit?: number } = {}) => `/api/admin/export/users.csv${qs(query)}`,
}
