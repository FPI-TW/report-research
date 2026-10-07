import { z } from 'zod'

/**
 * 目前登入身分與管理端點的回應契約，鏡像後端 `GET /api/me`、`/api/admin/*`。
 *
 * 時間欄位一律 ISO 字串或 null（nullish：舊後端少一鍵時不讓整頁 parse 失敗）。
 * `action`、`target_type` 刻意收成 string 而非 enum：後端新增一種管理動作時，enum 會讓
 * 整張稽核表 parse 失敗；收成 string，畫面只是多一列沒有中文標籤的代碼。
 */
export const roleSchema = z.enum(['admin', 'user'])
export type Role = z.infer<typeof roleSchema>

export const meSchema = z.object({
  id: z.string().nullable(),
  username: z.string(),
  role: roleSchema,
  // 以下由 Admin v1 加入；舊後端少這幾鍵時照樣 parse（nullish／預設值）。
  is_super: z.boolean().nullish(),
  scopes: z.array(z.string()).nullish(),
  elevated_until: z.string().nullish(),
  totp_enabled: z.boolean().nullish(),
  // Admin v2：管理員 TOTP 強制開啟而本人還沒開 TOTP（管理端點此時一律 403 mfa_enrollment_required）。
  mfa_enrollment_required: z.boolean().nullish(),
})
export type Me = z.infer<typeof meSchema>

export const adminUserSchema = z.object({
  id: z.string(),
  username: z.string(),
  role: roleSchema,
  enabled: z.boolean(),
  created_at: z.string().nullish(),
  updated_at: z.string().nullish(),
  password_changed_at: z.string().nullish(),
  last_login_at: z.string().nullish(),
  last_seen_at: z.string().nullish(),
  active_sessions: z.number(),
})
export type AdminUser = z.infer<typeof adminUserSchema>

export const adminUsersSchema = z.object({ items: z.array(adminUserSchema) })

export const forceLogoutSchema = z.object({ revoked: z.number() })

export const auditEntrySchema = z.object({
  id: z.number(),
  actor_user_id: z.string().nullable(),
  actor_username: z.string().nullable(),
  action: z.string(),
  target_type: z.string(),
  target_id: z.string().nullable(),
  // detail 是自由形狀的 jsonb；形狀不對時退回空物件，不讓一列畸形資料拖垮整張表。
  detail: z.record(z.string(), z.unknown()).catch({}),
  created_at: z.string().nullish(),
})
export type AuditEntry = z.infer<typeof auditEntrySchema>

export const auditPageSchema = z.object({
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
  has_more: z.boolean(),
  next_offset: z.number().nullable(),
  items: z.array(auditEntrySchema),
})
export type AuditPage = z.infer<typeof auditPageSchema>
