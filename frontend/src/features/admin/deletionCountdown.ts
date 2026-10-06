/** 距離執行時刻還有多久（「23 小時 5 分後刪除」）；已到期顯示「即將刪除」。 */
export function deletionCountdown(executeAfter: string, now: number): string {
  const ms = new Date(executeAfter).getTime() - now
  if (Number.isNaN(ms)) return '已排程刪除'
  if (ms <= 0) return '即將刪除'
  const mins = Math.ceil(ms / 60_000)
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return h > 0 ? `${h} 小時 ${m} 分後刪除` : `${m} 分後刪除`
}
