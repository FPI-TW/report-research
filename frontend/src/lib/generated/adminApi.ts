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

export const LogoutResponseSchema = z.object({
  revoked: z.number().int(),
})
export type LogoutResponse = z.infer<typeof LogoutResponseSchema>

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

export const OpsServiceDetailSchema = z.object({
  name: z.string(),
  kind: z.enum(['systemd', 'container']),
  tier: z.enum(['critical', 'important', 'supporting']),
  target: z.string(),
  timer: z.string().nullable().optional(),
  actions: z.array(z.enum(['status', 'logs'])),
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
  actions: z.array(z.enum(['status', 'logs'])),
  description: z.string().optional(),
  summary: z.enum(['running', 'idle', 'failed', 'transitioning', 'not_found', 'unknown']),
  error: z.string().nullable().optional(),
  systemd: OpsSystemdStateSchema.nullable().optional(),
  container: OpsContainerStateSchema.nullable().optional(),
  timer_state: OpsTimerStateSchema.nullable().optional(),
})
export type OpsServiceStatus = z.infer<typeof OpsServiceStatusSchema>

export const OpsServiceListResponseSchema = z.object({
  environment: z.string(),
  host: z.string(),
  checked_at: z.string(),
  items: z.array(OpsServiceStatusSchema),
})
export type OpsServiceListResponse = z.infer<typeof OpsServiceListResponseSchema>

function qs(query: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

export const adminApi = {
  /** GET /api/admin/audit — Audit Log */
  auditLog: (query: { limit?: number; offset?: number } = {}) => requestJSON(`/api/admin/audit${qs(query)}`, AuditResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/audit/verify — Verify Audit Chain */
  verifyAuditChain: () => requestJSON('/api/admin/audit/verify', AuditChainResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/deletions — List Deletions */
  listDeletions: (query: { status?: string } = {}) => requestJSON(`/api/admin/deletions${qs(query)}`, DeletionListResponseSchema, { cache: 'no-store' }),
  /** POST /api/admin/elevate — Elevate */
  elevate: (body: z.input<typeof ElevateRequestSchema>) => requestJSON('/api/admin/elevate', ElevateResponseSchema, jsonBody('POST', body)),
  /** GET /api/admin/ops/services — List Ops Services */
  listOpsServices: () => requestJSON('/api/admin/ops/services', OpsServiceListResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/ops/services/{name} — Get Ops Service */
  getOpsService: (name: string) => requestJSON(`/api/admin/ops/services/${encodeURIComponent(name)}`, OpsServiceDetailSchema, { cache: 'no-store' }),
  /** GET /api/admin/ops/services/{name}/logs — Get Ops Service Logs */
  getOpsServiceLogs: (name: string, query: { since?: string; lines?: number } = {}) => requestJSON(`/api/admin/ops/services/${encodeURIComponent(name)}/logs${qs(query)}`, OpsLogsResponseSchema, { cache: 'no-store' }),
  /** GET /api/admin/reports — List Reports */
  listReports: (query: { q?: string; hidden?: string; limit?: number; offset?: number } = {}) => requestJSON(`/api/admin/reports${qs(query)}`, AdminReportListResponseSchema, { cache: 'no-store' }),
  /** PUT /api/admin/reports/{file_hash}/visibility — Set Report Visibility */
  setReportVisibility: (fileHash: string, body: z.input<typeof ReportVisibilityRequestSchema>) => requestJSON(`/api/admin/reports/${encodeURIComponent(fileHash)}/visibility`, ReportVisibilityResponseSchema, jsonBody('PUT', body)),
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
