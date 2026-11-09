import { Link } from 'react-router'
import { Card } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import type { Coverage, Progress, Summary } from '../../../../lib/progressSchema'
import { Bar } from './parts'
import { DERIVED_LABEL } from './pipelineMeta'
import { fmtInt } from './rate'
import styles from './Pipeline.module.css'
import { t } from '@/i18n'

/**
 * 派生資產的覆蓋與最後產出。**只列數字、不判新舊**：多久算過期由「資料健康」的批次新鮮度判
 * （與每日 08:30 的 report-mark-freshness 同一組規則），兩邊各寫一套標準遲早對不上。
 *
 * 摘錄與訊號刻意只跑子集（近 30 天），全表覆蓋率不是訊號，`latest` 有沒有前進才是：
 * 批次停跑的症狀只是閱讀頁少一個區塊，沒有人會回報。
 */
function CoverageRow({ name, scope, cov }: { name: string; scope: string; cov: Summary | Coverage | undefined }) {
  if (!cov) {
    return (
      <tr>
        <td>{name} <span className={c.muted}>{scope}</span></td>
        <td colSpan={3} className={c.muted}>{t('admin.ops.pipeline.coverageCard.thisBackendVersionDoesnT')}</td>
      </tr>
    )
  }
  const latest = 'latest' in cov ? cov.latest : null
  return (
    <tr>
      <td>{name} <span className={c.muted}>{scope}</span></td>
      <td className={styles.barCell}><Bar pct={cov.pct} label={t('admin.ops.pipeline.coverageCard.coverage', { name })} /></td>
      <td className={`${c.num} ${c.tdRight}`}>
        {cov.pct.toFixed(1)}%
        <div className={c.sub}>{fmtInt(cov.done)}/{fmtInt(cov.total)}</div>
      </td>
      <td className={c.num}>{latest ?? <span className={c.muted}>—</span>}</td>
    </tr>
  )
}

export function CoverageCard({ progress: p }: { progress: Progress }) {
  return (
    <Card
      title={t('admin.ops.pipeline.coverageCard.outputCoverage')}
      labelledBy="pipeline-coverage-title"
      right={<Link className={`${c.btn} ${c.btnSm}`} to="../data-health" relative="path">{t('admin.ops.pipeline.coverageCard.viewBatchFreshness')}</Link>}
    >
      <div className={c.tableWrap}>
        <table className={c.table} aria-label={t('admin.ops.pipeline.coverageCard.outputCoverage')}>
          <thead>
            <tr><th>{t('admin.ops.pipeline.coverageCard.item')}</th><th>{t('admin.ops.pipeline.coverageCard.coverage2')}</th><th className={c.tdRight}>{t('admin.ops.pipeline.coverageCard.share')}</th><th>{t('admin.ops.pipeline.coverageCard.lastOutput')}</th></tr>
          </thead>
          <tbody>
            <CoverageRow name={t('admin.ops.pipeline.coverageCard.reportSummaries')} scope={t('admin.ops.pipeline.coverageCard.all')} cov={p.summary} />
            <CoverageRow name={DERIVED_LABEL.takeaways} scope={t('admin.ops.pipeline.coverageCard.last30Days')} cov={p.takeaway} />
            <CoverageRow name={DERIVED_LABEL.signals} scope={t('admin.ops.pipeline.coverageCard.last30Days')} cov={p.signal} />
          </tbody>
        </table>
      </div>
      <div className={`${c.cardFoot} ${styles.footNote}`}>
        <span>{t('admin.ops.pipeline.coverageCard.takeawaysAndSignalsIntentionallyRun')}</span>
      </div>
    </Card>
  )
}
