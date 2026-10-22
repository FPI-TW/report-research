/** 回應裡值得看的標頭（錯誤處理會用到的那幾個）。 */
const SHOWN_HEADERS = ['x-request-id', 'retry-after', 'www-authenticate', 'cache-control', 'content-type'] as const

export interface TryResult {
  status: number
  statusText: string
  ms: number
  headers: [string, string][]
  body: string
}

/**
 * 以 API 金鑰直接呼叫 `/external/v1/*`：與 API 用戶端走同一條路（Bearer 認證、不帶 cookie），
 * 所以拿到的狀態碼、錯誤碼與標頭就是對方會看到的。
 */
export async function sendTry(method: string, path: string, apiKey: string): Promise<TryResult> {
  const t0 = performance.now()
  const resp = await fetch(path, {
    method,
    headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
    credentials: 'omit',
    cache: 'no-store',
  })
  const raw = await resp.text()
  let body = raw
  try { body = JSON.stringify(JSON.parse(raw), null, 2) } catch { /* 非 JSON（閘道錯誤頁）：原樣顯示 */ }
  const headers = SHOWN_HEADERS.flatMap(h => {
    const v = resp.headers.get(h)
    return v == null ? [] : [[h, v] as [string, string]]
  })
  return { status: resp.status, statusText: resp.statusText, ms: Math.round(performance.now() - t0), headers, body }
}
