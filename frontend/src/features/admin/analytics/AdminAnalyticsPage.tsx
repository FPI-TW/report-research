import { useMemo, useState } from 'react'
import { ConsolePage, PageHeader, Segmented } from '../../../components/console'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { RequireScope } from '../RequireScope'
import { RANGES, sinceForDays, type RangeDays } from './analyticsFormat'
import { OperationsSection, OverviewSection, QualitySection, RoutesSection, TopSection } from './AnalyticsSections'
import type { AnalyticsQuery } from './useAnalytics'

// 後端的 ANALYTICS_MIN_USERS 預設 3；各區塊的「<k」以回應的 range.min_users 為準，這裡只用在說明文字。
const MIN_USERS_HINT = 3

function AnalyticsContent() {
  const [range, setRange] = useState<RangeDays>('30')
  const query = useMemo<AnalyticsQuery>(() => ({ since: sinceForDays(Number(range)) }), [range])
  return (
    <>
      <PageHeader
        summary="問答量、活躍、延遲、路由與品質的彙總；只有彙總，沒有依使用者拆分的視圖。"
        help={
          <>
            最近 90 天即時查詢，更早的日子讀每晚彙總。標的、研報、市場、路由類別這類可能看出個人偏好的格子，
            不重複使用者少於 {MIN_USERS_HINT} 位時顯示「&lt;{MIN_USERS_HINT}」、不給數字；熱門清單裡這類項目連名稱都不列，只計「另有幾項」。
            若只有一格被藏、可以用總數減其他格算回來，會再隱藏一格數值最小的（標示「隱藏」），讓被藏的那一格推算不出來。
            問答數、活躍人數、延遲與上傳審核量是總量，不設門檻。這裡不顯示任何問題或答案；日期以台北時間切日。
          </>
        }
        actions={<Segmented label="範圍" value={range} onChange={setRange} options={RANGES} />}
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
      <RequireScope scope="analytics.read" title="使用分析">
        <ConsolePage><AnalyticsContent /></ConsolePage>
      </RequireScope>
    </RequireAdmin>
  )
}
