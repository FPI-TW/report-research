import { useMemo, useState } from 'react'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { AdminHeader } from '../AdminHeader'
import { RequireScope } from '../RequireScope'
import { RANGES, sinceForDays } from './analyticsFormat'
import { OperationsSection, OverviewSection, QualitySection, RoutesSection, TopSection } from './AnalyticsSections'
import type { AnalyticsQuery } from './useAnalytics'
import adminStyles from '../Admin.module.css'
import styles from './Analytics.module.css'

// 後端的 ANALYTICS_MIN_USERS 預設 3；各區塊的「<k」以回應的 range.min_users 為準，這裡只用在說明文字。
const MIN_USERS_HINT = 3

function AnalyticsContent() {
  const [days, setDays] = useState(30)
  const query = useMemo<AnalyticsQuery>(() => ({ since: sinceForDays(days) }), [days])
  return (
    <>
      <section className={adminStyles.card} aria-label="範圍與隱私">
        <div className={styles.toolbar}>
          <label className={adminStyles.field}>
            範圍
            <select value={days} onChange={e => setDays(Number(e.target.value))}>
              {RANGES.map(r => <option key={r.days} value={r.days}>{r.label}</option>)}
            </select>
          </label>
        </div>
        <div className={adminStyles.spacer} />
        <p className={styles.privacy}>
          <b>只有彙總。</b>標的、研報、市場、路由類別這類可能看出個人偏好的格子，不重複使用者少於 {MIN_USERS_HINT} 位時顯示「&lt;{MIN_USERS_HINT}」、不給數字；
          熱門清單裡這類項目連名稱都不列，只計「另有幾項」。問答數、活躍人數、延遲與上傳審核量是總量，不設門檻。
          這裡沒有依使用者拆分的視圖，也不顯示任何問題或答案；日期以台北時間切日。
        </p>
      </section>
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
        <div className={adminStyles.page}>
          <div className={adminStyles.inner}>
            <AdminHeader
              title="使用分析"
              subtitle="問答量、活躍使用者、延遲、路由分布、品質趨勢、熱門研報與上傳審核量的彙總。最近 90 天即時查詢，更早的日子讀每晚彙總。"
            />
            <AnalyticsContent />
          </div>
        </div>
      </RequireScope>
    </RequireAdmin>
  )
}
