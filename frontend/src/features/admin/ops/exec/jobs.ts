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
