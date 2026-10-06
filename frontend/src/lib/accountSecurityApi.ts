import { z } from 'zod'
import { jsonBody, requestJSON } from './api'

/**
 * 自助帳號安全 `/api/me/*`（web/routers/account_security.py）的 zod 鏡像與呼叫函式。
 *
 * `/api/admin/*` 的 client 由 OpenAPI 產生（generated/adminApi.ts）；這幾支不在那個前綴底下，
 * 依其他 API 的慣例手寫。錯誤一律走 requestJSON：`ApiError.code` 帶 `totp_required`、
 * `elevation_required`、`bad_totp`、`totp_state` 等穩定代碼。
 */
export const totpStatusSchema = z.object({ enabled: z.boolean(), pending: z.boolean() })
export type TotpStatus = z.infer<typeof totpStatusSchema>

export const totpSetupSchema = z.object({ secret: z.string(), otpauth_uri: z.string() })
export type TotpSetup = z.infer<typeof totpSetupSchema>

export const elevateResponseSchema = z.object({ elevated_until: z.string() })

export const meSecurityApi = {
  totpStatus: () => requestJSON('/api/me/totp', totpStatusSchema, { cache: 'no-store' }),
  totpSetup: () => requestJSON('/api/me/totp/setup', totpSetupSchema, jsonBody('POST')),
  totpConfirm: (code: string) => requestJSON('/api/me/totp/confirm', totpStatusSchema, jsonBody('POST', { code })),
  totpDisable: () => requestJSON('/api/me/totp/disable', totpStatusSchema, jsonBody('POST')),
  elevate: (body: { password: string; code?: string | null }) =>
    requestJSON('/api/me/elevate', elevateResponseSchema, jsonBody('POST', body)),
}
