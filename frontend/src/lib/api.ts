import type { ZodType } from 'zod'

export class ApiError extends Error {
  status: number
  /** 後端統一錯誤格式的穩定代碼（例如 `elevation_required`）；非 JSON 回應時為 undefined。 */
  code?: string
  constructor(status: number, message: string, code?: string) {
    super(message)
    this.status = status
    this.code = code
    this.name = 'ApiError'
  }
}

/** 統一錯誤格式（web/errors.py）的 `code`；拿不到就 undefined。 */
export function errorCode(body: unknown): string | undefined {
  const code = (body && typeof body === 'object') ? (body as { code?: unknown }).code : undefined
  return typeof code === 'string' && code ? code : undefined
}

/** session 過期/未登入時導向登入頁，帶上目前 SPA 路徑供登入後返回。 */
export function redirectToLogin(): void {
  const next = location.pathname + location.search
  location.assign('/login?next=' + encodeURIComponent(next))
}

export async function getJSON<T>(path: string, schema: ZodType<T>, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, { ...init, credentials: 'same-origin' })
  if (resp.status === 401) {
    redirectToLogin()
    throw new ApiError(401, '未登入')
  }
  if (!resp.ok) throw new ApiError(resp.status, `HTTP ${resp.status}`)
  return schema.parse(await resp.json())
}

/**
 * FastAPI 錯誤回應的 `detail` → 給人看的一句話。
 * 400／409 是字串（後端已寫成中文，原樣顯示）；422 是陣列（取每項的 msg）；403 沒有 detail 時補一句。
 */
export function errorDetail(body: unknown, status: number): string {
  const detail = (body && typeof body === 'object') ? (body as { detail?: unknown }).detail : undefined
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const msgs = detail
      .map(d => (d && typeof d === 'object' && typeof (d as { msg?: unknown }).msg === 'string') ? (d as { msg: string }).msg : '')
      .filter(Boolean)
    if (msgs.length) return msgs.join('；')
  }
  if (status === 403) return '需要管理員權限'
  return `HTTP ${status}`
}

/**
 * 與 getJSON 相同（401 一樣導回登入），但非 2xx 時把後端 `detail` 放進 ApiError.message。
 * 管理頁要把「至少要保留一位啟用中的管理員」這類 409 原樣顯示給人看；getJSON 只給 `HTTP 409`。
 */
export async function requestJSON<T>(path: string, schema: ZodType<T>, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, { ...init, credentials: 'same-origin' })
  if (resp.status === 401) {
    redirectToLogin()
    throw new ApiError(401, '未登入')
  }
  if (!resp.ok) {
    let body: unknown = null
    try { body = await resp.json() } catch { /* 非 JSON（代理層錯誤頁）：只剩狀態碼可說 */ }
    throw new ApiError(resp.status, errorDetail(body, resp.status), errorCode(body))
  }
  return schema.parse(await resp.json())
}

/** 送 JSON body 的 RequestInit（管理頁的 POST／PATCH 共用）。 */
export function jsonBody(method: 'POST' | 'PATCH' | 'PUT', body?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }
}
