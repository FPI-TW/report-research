import { ApiError, errorCode, errorDetail, redirectToLogin } from '../../lib/api'

/** 一次 CSV 下載的結果（取自回應標頭；後端 web/csv_export.py）。 */
export type CsvDownload = { filename: string; rows: number | null; truncated: boolean }

/** `Content-Disposition: attachment; filename="report-mark-audit-20261006.csv"` → 檔名；拿不到用 fallback。 */
export function filenameFrom(disposition: string | null, fallback: string): string {
  const m = disposition?.match(/filename="([^"]+)"/)
  return m ? m[1] : fallback
}

/**
 * 以 fetch 取管理清單的 CSV（`/api/admin/export/*.csv`）並交給瀏覽器下載。
 *
 * 不直接用 `<a href download>`：那樣 403／503 時瀏覽器會把錯誤 JSON 存成檔案或整頁導走，看不到後端的
 * `detail`。這裡失敗時拋 `ApiError`（與 `requestJSON` 相同的訊息規則），401 一樣導回登入。
 * 每次匯出後端都會寫一筆稽核（`data.export`）。
 */
export async function downloadCsv(url: string, fallbackName = 'export.csv'): Promise<CsvDownload> {
  const resp = await fetch(url, { credentials: 'same-origin', cache: 'no-store' })
  if (resp.status === 401) {
    redirectToLogin()
    throw new ApiError(401, '未登入')
  }
  if (!resp.ok) {
    let body: unknown = null
    try { body = await resp.json() } catch { /* 非 JSON（代理層錯誤頁）：只剩狀態碼可說 */ }
    throw new ApiError(resp.status, errorDetail(body, resp.status), errorCode(body))
  }
  const blob = await resp.blob()
  const filename = filenameFrom(resp.headers.get('Content-Disposition'), fallbackName)
  const href = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = href
    a.download = filename
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    // 有些瀏覽器在 click 之後才開始讀 blob：下一輪再釋放。
    setTimeout(() => URL.revokeObjectURL(href), 0)
  }
  const rows = resp.headers.get('X-Export-Rows')
  return {
    filename,
    rows: rows != null && rows !== '' && !Number.isNaN(Number(rows)) ? Number(rows) : null,
    truncated: resp.headers.get('X-Export-Truncated') === 'true',
  }
}
