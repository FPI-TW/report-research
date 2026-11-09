import { useSyncExternalStore } from 'react'
import { z } from 'zod'
import { MARKET_ORDER, setMarketLabelOverrides } from './meta'

/**
 * profile 啟用的市場子集與顯示名稱（`GET /api/markets`；後端 `web/market_scope.py`，設定來源 `app/profile.py` 的
 * `[markets]`）。
 *
 * 市場代碼本身是固定的標準詞彙（`meta.ts` 的 `MARKET_ORDER`，API 回應的 zod enum 仍以它為準：研報可能標到
 * 未啟用的市場）。profile 決定的只有兩件事：使用者端的選單列哪些、依什麼順序（`useEnabledMarkets`），以及顯示名稱
 * （寫進 `meta.ts` 的覆寫，`marketLabel` 讀它）。
 *
 * 顯示名稱是在抓到之後才換：已經畫好、之後不再重繪的元件會留著標準名稱直到下次重繪。實務上影響很小——
 * 啟動時就開始抓，而列出研報的頁面都要等自己的資料回來才畫市場標籤。
 *
 * 抓到之前與抓不到時＝全部標準市場、標準名稱（與 profile 機制上線前相同）；`loadMarkets()` 在 `main.tsx` 啟動時
 * 抓一次，失敗只記警告、不導向登入頁（品牌與選單不是擋頁的理由）。
 */
export const marketsResponseSchema = z.object({
  markets: z.array(z.string()).optional(),
  items: z.array(z.object({ code: z.string(), label: z.string() })).optional(),
  labels: z.record(z.string(), z.string()).optional(),
})

export interface MarketsConfig {
  enabled: readonly string[]
  labels: Readonly<Record<string, string>>
}

export const DEFAULT_MARKETS: MarketsConfig = { enabled: MARKET_ORDER, labels: {} }

const STANDARD = new Set<string>(MARKET_ORDER)

export function fromResponse(raw: z.infer<typeof marketsResponseSchema>): MarketsConfig {
  // 只收標準代碼：不認得的代碼放進選單只會換來 422。
  const enabled = (raw.items?.map(i => i.code) ?? raw.markets ?? []).filter(c => STANDARD.has(c))
  const labels: Record<string, string> = {}
  for (const [code, label] of Object.entries(raw.labels ?? {})) if (STANDARD.has(code) && label) labels[code] = label
  for (const item of raw.items ?? []) if (STANDARD.has(item.code) && item.label) labels[item.code] = item.label
  return { enabled: enabled.length ? enabled : MARKET_ORDER, labels }
}

let current: MarketsConfig = DEFAULT_MARKETS
const listeners = new Set<() => void>()

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

function getSnapshot(): MarketsConfig {
  return current
}

/** 目前的設定（非 React 的呼叫端用，例如 `marketLabel`）。 */
export function marketsConfig(): MarketsConfig {
  return current
}

/** 寫入 store（`loadMarkets` 抓到時呼叫；測試也用它，傳 null 還原成預設）。 */
export function setMarketsConfig(next: MarketsConfig | null): void {
  const value = next ?? DEFAULT_MARKETS
  if (value === current) return
  current = value
  setMarketLabelOverrides(value.labels)
  listeners.forEach((l) => l())
}

export function useMarketsConfig(): MarketsConfig {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}

/** 使用者端選單的市場（profile 的顯示順序）。 */
export function useEnabledMarkets(): readonly string[] {
  return useMarketsConfig().enabled
}

export async function loadMarkets(): Promise<MarketsConfig> {
  try {
    const resp = await fetch('/api/markets', { credentials: 'same-origin', cache: 'no-store' })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const config = fromResponse(marketsResponseSchema.parse(await resp.json()))
    setMarketsConfig(config)
    return config
  } catch (err) {
    console.warn('讀不到市場設定，改用全部標準市場', err)
    return current
  }
}
