import type { ZodType } from 'zod'
import { progressSchema, type ProgressResponse } from './schemas'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

/** session 過期 / 未登入時導向登入頁，並帶上目前 SPA 路徑供登入後返回。 */
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

export function getProgress(): Promise<ProgressResponse> {
  return getJSON('/api/progress', progressSchema, { cache: 'no-store' })
}

/** SSE helper 介面占位：ask/report 後續 spec 實作（本切片不使用）。 */
export type SseHandlers = {
  onDelta?: (text: string) => void
  onDone?: (payload: unknown) => void
  onError?: (err: unknown) => void
}
