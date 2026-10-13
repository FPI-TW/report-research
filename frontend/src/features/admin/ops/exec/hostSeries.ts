import type { ObservationItem, ObservationListResponse } from '../../../../lib/generated/adminApi'
import type { TrendBar } from '../../../../components/console'

/** 每種粒度一個觀測區間有多長（秒）：原始觀測每 60 秒一筆，聚合是 5 分鐘或 1 小時一桶。 */
const STEP_SECONDS: Record<ObservationListResponse['resolution'], number> = { raw: 60, '5m': 300, '1h': 3600 }
const MAX_SLOTS = 120
const DAY_MS = 86_400_000

const pad = (n: number) => String(n).padStart(2, '0')

function hhmm(ms: number): string {
  const d = new Date(ms)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 刻度與柱子的時間：一天以內只寫「時:分」，跨好幾天的範圍（7 天）前面帶「月/日」才分得出是哪一天。 */
function tick(ms: number, withDate: boolean): string {
  if (!withDate) return hhmm(ms)
  const d = new Date(ms)
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${hhmm(ms)}`
}

interface Grid { start: number; end: number; step: number; slots: number; width: number; withDate: boolean }

/** 查詢區間怎麼切格：一格至少一個觀測區間，最多 120 格。區間不合理（迄不晚於起）時回 null。 */
function grid(data: ObservationListResponse): Grid | null {
  const start = new Date(data.since).getTime()
  const end = new Date(data.until).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null
  const step = STEP_SECONDS[data.resolution] * 1000
  const slots = Math.min(MAX_SLOTS, Math.max(1, Math.round((end - start) / step)))
  return { start, end, step, slots, width: (end - start) / slots, withDate: end - start > DAY_MS }
}

/**
 * 把某個 subject／metric 的觀測切成等寬的時間格（舊→新），給趨勢長條圖用。
 * 一格取最高值（聚合的桶看 `value_max`），和數字磚上的「最高」是同一個口徑；沒有觀測的格子是 null（圖上留空）。
 */
export function trendSeries(data: ObservationListResponse, subject: string, metric: string): TrendBar[] {
  const g = grid(data)
  if (!g) return []
  // 聚合的桶以起點標時間，而第一個桶對齊到 since 所在的桶起點（可能比 since 早不到一個桶）：它涵蓋的時間
  // 仍在區間內，算進第一格。原始觀測沒有這個問題，區間外的一律不算。
  const earliest = data.resolution === 'raw' ? g.start : g.start - g.step + 1
  const values: (number | null)[] = Array.from({ length: g.slots }, () => null)
  for (const it of data.items) {
    if (it.subject !== subject || it.metric !== metric) continue
    const v = it.value_max ?? it.value
    const at = new Date(it.observed_at).getTime()
    if (v == null || !Number.isFinite(at) || at < earliest || at > g.end) continue
    const i = Math.min(g.slots - 1, Math.max(0, Math.floor((at - g.start) / g.width)))
    const cur = values[i]
    if (cur == null || v > cur) values[i] = v
  }
  return values.map((value, i) => ({ value, label: tick(g.start + i * g.width, g.withDate) }))
}

/** 圖下方的時間刻度：起、中、迄。 */
export function trendAxis(data: ObservationListResponse): string[] {
  const g = grid(data)
  if (!g) return []
  return [g.start, (g.start + g.end) / 2, g.end].map(ms => tick(ms, g.withDate))
}

/** 一根柱子涵蓋多久（「1 分鐘」「12 分鐘」「1.4 小時」），寫在圖的說明裡；區間不合理時回 null。 */
export function trendSlotSpan(data: ObservationListResponse): string | null {
  const g = grid(data)
  if (!g) return null
  const minutes = g.width / 60_000
  if (minutes < 90) return `${Math.max(1, Math.round(minutes))} 分鐘`
  return `${(minutes / 60).toFixed(1).replace(/\.0$/, '')} 小時`
}

/** 這段期間的最高值（聚合的桶 value 是平均，最高值要看 value_max）。 */
export function peak(items: ObservationItem[], subject: string, metric: string): number | null {
  let best: number | null = null
  for (const it of items) {
    const v = it.value_max ?? it.value
    if (it.subject === subject && it.metric === metric && v != null && (best == null || v > best)) best = v
  }
  return best
}
