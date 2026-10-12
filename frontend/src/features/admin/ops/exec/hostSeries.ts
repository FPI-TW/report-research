import type { ObservationItem, ObservationListResponse } from '../../../../lib/generated/adminApi'
import type { TrendBar } from '../../../../components/console'

/** 每種粒度一個觀測區間有多長（秒）：原始觀測每 60 秒一筆，聚合是 5 分鐘或 1 小時一桶。 */
const STEP_SECONDS: Record<ObservationListResponse['resolution'], number> = { raw: 60, '5m': 300, '1h': 3600 }
const MAX_SLOTS = 120

function hhmm(ms: number): string {
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * 把某個 subject／metric 的觀測切成等寬的時間格（舊→新），給趨勢長條圖用。
 * 一格取最高值（聚合的桶看 `value_max`），和數字磚上的「最高」是同一個口徑；沒有觀測的格子是 null（圖上留空）。
 */
export function trendSeries(data: ObservationListResponse, subject: string, metric: string): TrendBar[] {
  const start = new Date(data.since).getTime()
  const end = new Date(data.until).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return []
  const step = STEP_SECONDS[data.resolution] * 1000
  const slots = Math.min(MAX_SLOTS, Math.max(1, Math.round((end - start) / step)))
  const width = (end - start) / slots
  const values: (number | null)[] = Array.from({ length: slots }, () => null)
  for (const it of data.items) {
    if (it.subject !== subject || it.metric !== metric) continue
    const v = it.value_max ?? it.value
    const at = new Date(it.observed_at).getTime()
    if (v == null || !Number.isFinite(at) || at < start || at > end) continue
    const i = Math.min(slots - 1, Math.floor((at - start) / width))
    const cur = values[i]
    if (cur == null || v > cur) values[i] = v
  }
  return values.map((value, i) => ({ value, label: hhmm(start + i * width) }))
}

/** 圖下方的時間刻度：起、中、迄。 */
export function trendAxis(data: ObservationListResponse): string[] {
  const start = new Date(data.since).getTime()
  const end = new Date(data.until).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return []
  return [hhmm(start), hhmm((start + end) / 2), hhmm(end)]
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
