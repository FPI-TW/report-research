import type { TrendBar } from '../../../../components/console'
import type { LlmUsageDay } from '../../../../lib/generated/adminApi'

const DAY_MS = 86_400_000

/** 時間點在某個時區是哪一天（`YYYY-MM-DD`）；後端的 `by_day` 用同一個時區切日（台北）。 */
export function dayIn(iso: string, timeZone: string): string | null {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return null
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(at)
  } catch {
    return null
  }
}

function mmdd(day: string): string {
  return `${day.slice(5, 7)}/${day.slice(8, 10)}`
}

/**
 * 每日呼叫次數的長條（舊→新，一天一根）。後端的 `by_day` 只列有呼叫的日子：區間內沒列的日子是 0 次，
 * 補上才看得出哪幾天沒跑。`knownFrom` 之前的日子一律是 null（圖上留空）：用量檔超過讀取上限時，
 * 更早的紀錄沒讀到，不能畫成 0，也不能只畫讀得到的那一部分。
 */
export function dailyCalls(
  byDay: readonly Pick<LlmUsageDay, 'day' | 'calls'>[], sinceDay: string, untilDay: string, knownFrom?: string | null,
): TrendBar[] {
  const start = Date.parse(`${sinceDay}T00:00:00Z`)
  const end = Date.parse(`${untilDay}T00:00:00Z`)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return []
  const calls = new Map(byDay.map(d => [d.day, d.calls]))
  const bars: TrendBar[] = []
  for (let t = start; t <= end; t += DAY_MS) {
    const day = new Date(t).toISOString().slice(0, 10)
    const value = knownFrom && day < knownFrom ? null : calls.get(day) ?? 0
    bars.push({ value, label: day })
  }
  return bars
}

/** 圖下方的刻度：起、中、迄（MM/DD）。 */
export function dailyAxis(bars: readonly TrendBar[]): string[] {
  if (bars.length === 0) return []
  if (bars.length < 3) return bars.map(b => mmdd(b.label))
  return [bars[0], bars[Math.floor((bars.length - 1) / 2)], bars[bars.length - 1]].map(b => mmdd(b.label))
}
