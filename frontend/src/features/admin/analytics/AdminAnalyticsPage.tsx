import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { AdminComingSoon } from '../AdminComingSoon'
import { RequireScope } from '../RequireScope'

/** 使用分析（/app/admin/analytics；Admin v2 Analytics lane）。後端 /api/admin/analytics/*（analytics.read）。 */
export default function AdminAnalyticsPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="analytics.read" title="使用分析">
        <AdminComingSoon
          title="使用分析"
          subtitle="問答量、活躍使用者、延遲、路由分布與熱門研報的彙總。少於 3 位使用者的主題格子不顯示，沒有依使用者拆分的視圖。"
        />
      </RequireScope>
    </RequireAdmin>
  )
}
