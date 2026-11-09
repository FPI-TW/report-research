import { Button, PageHeader } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import { SECTION_ID } from './attention'
import { CorpusCard } from './CorpusCard'
import { CoverageCard } from './CoverageCard'
import { PipelineSummary } from './PipelineSummary'
import { ExtractionCard, FaithfulnessCard } from './QualityCards'
import { ScheduleCard } from './ScheduleCard'
import { PROGRESS_POLL_MS, useProgress } from './useProgress'
import { useRates } from './useRates'
import styles from './Pipeline.module.css'
import { t } from '@/i18n'

/**
 * 管線（/app/admin/operations/pipeline）：研報導入管線的即時狀態，原本是主平台的監控頁（/app/monitor），
 * 限管理員之後搬進維運。照「管理員打開它要回答的問題」排：有沒有壞 → 排程跑到哪 → 產出跟上沒 →
 * 品質 → 語料組成。
 *
 * 資料只來自 `/api/progress`（web 行程直接讀 DB 與 data/ 的 log），**不經維運代理**：代理停掉、
 * 監控收集沒裝的主機上這頁照常。外殼（`OperationsLayout`）沒有 `ops.read` 就不掛這頁、不發請求；
 * 真正擋人的是後端：`/api/progress` 限管理員＋`ops.read`。
 */
export default function OpsPipelinePage() {
  const q = useProgress()
  const rates = useRates(q.data, q.dataUpdatedAt)
  const header = (
    <PageHeader
      summary={t('admin.ops.pipeline.opsPipelinePage.liveStateOfTheReport')}
      help={(
        <>
          {t('admin.ops.pipeline.opsPipelinePage.dataComesOnlyFrom')}{' '}<code>/api/progress</code>{t('admin.ops.pipeline.opsPipelinePage.theWebProcessReadsThe', { v: PROGRESS_POLL_MS / 1000 })}
        </>
      )}
      actions={(
        <Button onClick={() => { void q.refetch() }} disabled={q.isFetching}>
          {q.isFetching ? t('admin.ops.pipeline.opsPipelinePage.refreshing') : t('admin.ops.pipeline.opsPipelinePage.refresh')}
        </Button>
      )}
    />
  )
  if (q.isPending) return <>{header}<p className={c.empty}>{t('admin.ops.pipeline.opsPipelinePage.loading')}</p></>
  if (!q.data) {
    const msg = q.error instanceof Error && q.error.message ? q.error.message : t('admin.ops.pipeline.opsPipelinePage.couldnTLoadTryAgain')
    return <>{header}<p className={c.error} role="alert">{t('admin.ops.pipeline.opsPipelinePage.couldnTLoadPipelineStatus', { msg })}</p></>
  }
  const p = q.data
  return (
    <>
      {header}
      <PipelineSummary progress={p} stale={q.isError} />
      <ScheduleCard progress={p} rates={rates} />
      <CoverageCard progress={p} />
      <div id={SECTION_ID.quality} className={styles.pair}>
        <FaithfulnessCard evaluation={p.evaluation} />
        <ExtractionCard extraction={p.extraction} />
      </div>
      <CorpusCard db={p.db} />
      <p className={styles.foot}>{t('admin.ops.pipeline.opsPipelinePage.dataFromApiProgressServer')}</p>
    </>
  )
}
