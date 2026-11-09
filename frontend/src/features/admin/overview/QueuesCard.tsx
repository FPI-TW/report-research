import { Link } from 'react-router'
import { Card } from '../../../components/console'
import type { AdminSignals } from '../adminSignals'
import type { SourceState } from '../useAdminSignals'
import { useFailedUploads, useOverviewTotp } from './overviewData'
import styles from '../Overview.module.css'
import { t } from '@/i18n'

interface Cell { key: string; to: string; label: string; n: string; meta?: string }

const fmtN = (n: number) => n.toLocaleString('zh-TW')
/** 還沒讀到或讀不到：不寫 0（0 會被讀成「沒有待處理」）。 */
const unread = (failed: boolean) => ({ n: '—', meta: failed ? t('admin.overview.queuesCard.unavailable') : t('admin.overview.queuesCard.loading') })

/**
 * 「待處理」：常態非零的佇列，只給數字、不亮燈。待複核與待審草稿取自導覽燈號同一份快取；
 * 上傳失敗與未開 2FA 的管理員是總覽自己查的。沒有對應 scope 的格子不畫、也不送請求。
 */
export function QueuesCard({ scopes, signals, sources, to }: {
  scopes: readonly string[]
  signals: AdminSignals
  sources: Record<'reviews' | 'uploads', SourceState>
  to: { reviews: string; uploads: string; security: string }
}) {
  const canUploads = scopes.includes('reports.manage')
  const canSecurity = scopes.includes('audit.read')
  const failed = useFailedUploads(canUploads)
  const totp = useOverviewTotp(canSecurity)
  const cells: Cell[] = []

  if (sources.reviews !== 'off') {
    const s = sources.reviews === 'ready' ? signals.reviews : undefined
    cells.push({
      key: 'reviews', to: to.reviews, label: t('admin.overview.queuesCard.reviewQueue'),
      ...(s ? { n: fmtN(s.count ?? 0), meta: s.detail } : unread(sources.reviews === 'error')),
    })
  }
  if (sources.uploads !== 'off') {
    const s = sources.uploads === 'ready' ? signals.uploads : undefined
    cells.push({
      key: 'drafts', to: `${to.uploads}?tab=draft`, label: t('admin.overview.queuesCard.draftsAwaitingReview'),
      ...(s ? { n: fmtN(s.count ?? 0), meta: t('admin.overview.queuesCard.uploadedWaitingToBePublished') } : unread(sources.uploads === 'error')),
    })
  }
  if (canUploads) {
    cells.push({
      key: 'failed', to: `${to.uploads}?tab=failed`, label: t('admin.overview.queuesCard.uploadProcessingFailed'),
      ...(failed.data != null
        ? { n: fmtN(failed.data), meta: failed.data > 0 ? t('admin.overview.queuesCard.retryOrReject') : t('admin.overview.queuesCard.noFailedUploads') }
        : unread(failed.isError)),
    })
  }
  if (canSecurity) {
    const without = totp.data?.admins_without_totp
    cells.push({
      key: 'totp', to: to.security, label: t('admin.overview.queuesCard.adminsWithoutTwoStepVerification'),
      ...(without
        ? { n: fmtN(without.length), meta: without.length ? adminNames(without.map(a => a.username)) : t('admin.overview.queuesCard.allAdminsHaveItOn') }
        : unread(totp.isError)),
    })
  }
  if (cells.length === 0) return null

  return (
    <Card title={t('admin.overview.queuesCard.toHandle')} labelledBy="overview-queues">
      <div className={styles.queues}>
        {cells.map(q => (
          <Link key={q.key} to={q.to} className={styles.queue}>
            <span className={styles.queueN}>{q.n}</span>
            <span className={styles.queueLabel}>{q.label}</span>
            {q.meta && <span className={styles.queueMeta}>{q.meta}</span>}
          </Link>
        ))}
      </div>
    </Card>
  )
}

/** 帳號最多列三個，其餘只說還有幾位（格子很窄）。 */
function adminNames(names: string[]): string {
  return names.length <= 3 ? names.join(t('admin.overview.queuesCard.text')) : t('admin.overview.queuesCard.andTotal', { slice: names.slice(0, 3).join(t('admin.overview.queuesCard.text')), length: names.length })
}
