/** 距離執行時刻還有多久（「23 小時 5 分後刪除」）；已到期顯示「即將刪除」。 */
import { t } from '@/i18n'

export function deletionCountdown(executeAfter: string, now: number): string {
  const ms = new Date(executeAfter).getTime() - now
  if (Number.isNaN(ms)) return t('admin.deletionCountdown.deletionScheduled')
  if (ms <= 0) return t('admin.deletionCountdown.deletingSoon')
  const mins = Math.ceil(ms / 60_000)
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return h > 0 ? t('admin.deletionCountdown.deletesInHMin', { h, m }) : t('admin.deletionCountdown.deletesInMin', { m })
}
