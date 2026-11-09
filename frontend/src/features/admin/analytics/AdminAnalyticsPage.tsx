import { useMemo, useState } from 'react'
import { ConsolePage, PageHeader, Segmented } from '../../../components/console'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { RequireScope } from '../RequireScope'
import { RANGES, sinceForDays, type RangeDays } from './analyticsFormat'
import { OperationsSection, OverviewSection, QualitySection, RoutesSection, TopSection } from './AnalyticsSections'
import type { AnalyticsQuery } from './useAnalytics'
import { t } from '@/i18n'

// 後端的 ANALYTICS_MIN_USERS 預設 3；各區塊的「<k」以回應的 range.min_users 為準，這裡只用在說明文字。
const MIN_USERS_HINT = 3

function AnalyticsContent() {
  const [range, setRange] = useState<RangeDays>('30')
  const query = useMemo<AnalyticsQuery>(() => ({ since: sinceForDays(Number(range)) }), [range])
  return (
    <>
      <PageHeader
        summary={t('admin.analytics.adminAnalyticsPage.aggregatesOfQAVolume')}
        help={
          <>
            {t('admin.analytics.adminAnalyticsPage.theLast90DaysAre', { MIN_USERS_HINT })}
          </>
        }
        actions={<Segmented label={t('admin.analytics.adminAnalyticsPage.range')} value={range} onChange={setRange} options={RANGES} />}
      />
      <OverviewSection query={query} />
      <TopSection query={query} />
      <RoutesSection query={query} />
      <QualitySection query={query} />
      <OperationsSection query={query} />
    </>
  )
}

/** 使用分析（/app/admin/analytics；Admin v2 Analytics lane）。後端 /api/admin/analytics/*（analytics.read）。 */
export default function AdminAnalyticsPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="analytics.read" title={t('admin.analytics.adminAnalyticsPage.analytics')}>
        <ConsolePage><AnalyticsContent /></ConsolePage>
      </RequireScope>
    </RequireAdmin>
  )
}
