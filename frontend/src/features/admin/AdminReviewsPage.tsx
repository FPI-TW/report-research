import { useQuery } from '@tanstack/react-query'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import { getJSON } from '../../lib/api'
import { useHasScope } from '../../lib/useMe'
import { judgeScaleSchema } from '../../lib/reviewSchemas'
import { AdminHeader } from './AdminHeader'
import { ReviewQueuePanel } from './ReviewQueuePanel'
import styles from './Admin.module.css'

/**
 * 待複核佇列（忠實度低分／倒讚／抽取品質）。原本在主平台的監控頁，限管理員之後搬來這裡。
 *
 * 判定尺資訊（換尺頭幾天要標「新量尺」）來自 `/api/review/judge-scale`（與佇列同樣要 `review.manage`），
 * 不讀 `/api/progress`：那支要 `ops.read`，而且帶整份管線與 runtime 資料。這裡只取一次、不輪詢，
 * 失敗就不標（那只是附註，不擋佇列）。
 */
function AdminReviews() {
  // 只決定要不要露出「查看內容」；沒有 scope 的人硬打端點會被後端 403 missing_scope。
  const canReadContent = useHasScope('qa_content.read')
  const scale = useQuery({
    queryKey: ['review-judge-scale'],
    queryFn: () => getJSON('/api/review/judge-scale', judgeScaleSchema, { cache: 'no-store' }),
    staleTime: 5 * 60_000,
    retry: false,
  })
  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <AdminHeader title="待複核" subtitle="系統偵測到需要人看的問答與研報；處理狀態、註記與處理人另存，原始品質訊號不變。" />
        <ReviewQueuePanel scale={scale.data ?? null} canReadContent={canReadContent} />
      </div>
    </div>
  )
}

export default function AdminReviewsPage() {
  return <RequireAdmin><AdminReviews /></RequireAdmin>
}
