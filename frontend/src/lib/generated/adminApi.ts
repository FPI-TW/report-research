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

export const DataHealthResponseSchema = z.object({
  generated_at: z.string(),
  overall: z.enum(['ok', 'warn', 'fail', 'unknown']),
  freshness: FreshnessSectionSchema,
  db_audit: DbAuditSectionSchema,
  r2_reconcile: R2ReconcileSectionSchema,
})
export type DataHealthResponse = z.infer<typeof DataHealthResponseSchema>

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
  /** GET /api/admin/audit — Audit Log */
  auditLog: (query: { limit?: number; offset?: number } = {}) => requestJSON(`/api/admin/audit${qs(query)}`, AuditResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/audit/verify — Verify Audit Chain */
  verifyAuditChain: () => requestJSON('/api/admin/audit/verify', AuditChainResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/data-health — Get Data Health */
  getDataHealth: () => requestJSON('/api/admin/data-health', DataHealthResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/deletions — List Deletions */
  listDeletions: (query: { status?: 'pending' | 'all' } = {}) => requestJSON(`/api/admin/deletions${qs(query)}`, DeletionListResponseSchema, { cache: 'no-store' }),
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
  listReports: (query: { q?: string | null; hidden?: boolean | null; limit?: number; offset?: number } = {}) => requestJSON(`/api/admin/reports${qs(query)}`, AdminReportListResponseSchema, { cache: 'no-store' }),
  /** PUT /api/admin/reports/{file_hash}/visibility — Set Report Visibility */
  setReportVisibility: (fileHash: string, body: z.input<typeof ReportVisibilityRequestSchema>) => requestJSON(`/api/admin/reports/${encodeURIComponent(fileHash)}/visibility`, ReportVisibilityResponseSchema, jsonBody('PUT', body)),
  /** GET /api/admin/retrieval-regression — Get Retrieval Regression */
  getRetrievalRegression: () => requestJSON('/api/admin/retrieval-regression', RetrievalRegressionResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/users — List Users */
  listUsers: () => requestJSON('/api/admin/users', UserListResponseSchema, { cache: 'no-store' }),
  /** POST /api/admin/users — Create User */
  createUser: (body: z.input<typeof CreateUserRequestSchema>) => requestJSON('/api/admin/users', UserItemSchema, jsonBody('POST', body)),
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

/** CSV 下載端點的網址（`text/csv`，不是 JSON）。 */
export const adminCsvUrls = {
  /** GET /api/admin/export/audit.csv — Export Audit（下載網址） */
  exportAudit: (query: { limit?: number } = {}) => `/api/admin/export/audit.csv${qs(query)}`,
  /** GET /api/admin/export/incidents.csv — Export Incidents（下載網址） */
  exportIncidents: (query: { status?: 'firing' | 'resolved' | 'lost' | null; component?: string | null; since?: string | null; until?: string | null; limit?: number } = {}) => `/api/admin/export/incidents.csv${qs(query)}`,
  /** GET /api/admin/export/jobs.csv — Export Jobs（下載網址） */
  exportJobs: (query: { service?: string | null; unit?: string | null; state?: 'running' | 'finished' | 'lost' | null; result?: string | null; since?: string | null; until?: string | null; limit?: number } = {}) => `/api/admin/export/jobs.csv${qs(query)}`,
  /** GET /api/admin/export/reports.csv — Export Reports（下載網址） */
  exportReports: (query: { q?: string | null; hidden?: boolean | null; limit?: number } = {}) => `/api/admin/export/reports.csv${qs(query)}`,
  /** GET /api/admin/export/users.csv — Export Users（下載網址） */
  exportUsers: (query: { limit?: number } = {}) => `/api/admin/export/users.csv${qs(query)}`,
}
