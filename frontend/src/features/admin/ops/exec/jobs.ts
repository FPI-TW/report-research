import type { JobItem } from '../../../../lib/generated/adminApi'

export const LOST_HINT = '沒觀測到結束，之後已有新一輪開始（收集器停機或兩次觀測之間跑完）'

/** 失敗＝已結束但 Result 不是 success。執行中與結果不明都不算失敗。 */
export function jobFailed(job: JobItem): boolean {
  return job.state === 'finished' && job.result !== 'success'
}

/** 日誌頁只收這四種時間範圍：挑涵蓋得到這次執行開始時間的最小一個；超過 1 天就涵蓋不到。 */
const LOG_WINDOWS: readonly [string, number][] = [['15m', 900], ['1h', 3600], ['6h', 21_600], ['1d', 86_400]]

export function logWindowFor(startedAt: string, now: string): { since: string; covered: boolean } {
  const age = (new Date(now).getTime() - new Date(startedAt).getTime()) / 1000
  if (!Number.isFinite(age)) return { since: '1h', covered: true }
  const hit = LOG_WINDOWS.find(([, seconds]) => age <= seconds)
  return hit ? { since: hit[0], covered: true } : { since: '1d', covered: false }
}

/** 與後端 `list_jobs` 的 `unit` 參數同一個格式與長度上限（`web/routers/admin_monitoring.py`）。 */
const UNIT_RE = /^[A-Za-z0-9][A-Za-z0-9@._:-]*\.service$/
const UNIT_MAX_LENGTH = 208
/** 後端回的 `started_at`（ISO 8601，帶時區）。 */
const AT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/
/** 以開始時間為中心、前後各 5 分鐘：同一個 unit 在這麼短的時間內不會有幾筆。 */
const LOCATE_HALF_WINDOW_MS = 5 * 60_000

/** 分享連結裡定位單次執行用的條件：哪個 unit、開始時間落在哪一小段。 */
export interface JobLocator { unit: string; since: string; until: string }

/**
 * 網址的 `unit`、`at`（使用者可改）→ 查詢條件。任何一個缺了或格式不對就回 null，呼叫端不送請求——
 * 不把任意字串交給後端，也免得一個改壞的連結換來一則 422。
 */
export function jobLocator(unit: string | null, at: string | null): JobLocator | null {
  if (!unit || unit.length > UNIT_MAX_LENGTH || !UNIT_RE.test(unit)) return null
  if (!at || !AT_RE.test(at)) return null
  const center = new Date(at).getTime()
  if (!Number.isFinite(center)) return null
  return {
    unit,
    since: new Date(center - LOCATE_HALF_WINDOW_MS).toISOString(),
    until: new Date(center + LOCATE_HALF_WINDOW_MS).toISOString(),
  }
}
