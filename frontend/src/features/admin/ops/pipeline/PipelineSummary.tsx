import { Link } from 'react-router'
import { Card, Chip, Lamp, type LampTone } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import type { Progress } from '../../../../lib/progressSchema'
import { SECTION_ID, attentionLevel, pipelineAttention, type AttentionTarget } from './attention'
import { PIPELINE_ROWS } from './pipelineMeta'
import { PROGRESS_POLL_MS } from './useProgress'
import { fmtInt } from './rate'
import styles from './Pipeline.module.css'
import { t } from '@/i18n'

const TARGET_TEXT: Record<AttentionTarget, string> = { get schedule() { return t('admin.ops.pipeline.pipelineSummary.viewScheduleAndRuns') }, get quality() { return t('admin.ops.pipeline.pipelineSummary.viewQuality') } }

const VERDICT: Record<'ok' | 'warn' | 'bad', { lamp: LampTone; text: (n: number) => string }> = {
  ok: { lamp: 'ok', text: () => t('admin.ops.pipeline.pipelineSummary.pipelineOk') },
  warn: { lamp: 'warn', text: n => t('admin.ops.pipeline.pipelineSummary.needsAttention', { n }) },
  bad: { lamp: 'bad', text: n => t('admin.ops.pipeline.pipelineSummary.errors', { n }) },
}

/**
 * 結論：一句話＋逐條原因（判準見 attention.ts）＋中性的「待人看」。
 * `stale`：重抓失敗、畫面還是上一筆（keepPreviousData）時明說，不讓舊資料冒充即時。
 * 「重新整理」在頁首（`OpsPipelinePage` 的 PageHeader）。
 */
export function PipelineSummary({ progress: p, stale }: { progress: Progress; stale: boolean }) {
  const items = pipelineAttention(p)
  const level = attentionLevel(items)
  const running = PIPELINE_ROWS.filter(r => r.key !== 'web' && p.pipelines[r.key]).length
  const qa = p.evaluation?.qa
  const review: string[] = []
  if (qa) review.push(t('admin.ops.pipeline.pipelineSummary.belowTheFaithfulnessThreshold', { below_min: fmtInt(qa.below_min) }))
  if (p.extraction) review.push(t('admin.ops.pipeline.pipelineSummary.reportsNeedExtractionReview', { needs_review: fmtInt(p.extraction.needs_review) }))

  return (
    <Card title={t('admin.ops.pipeline.pipelineSummary.pipelineStatus')} labelledBy="pipeline-summary-title">
      <div className={styles.body}>
        <div className={styles.verdictRow}>
          <div className={styles.verdict}>
            {/* 結論的文字已經說明狀態，燈號當裝飾 */}
            <Lamp tone={VERDICT[level].lamp} label="" size={8} />
            <span className={styles.verdictText}>{VERDICT[level].text(items.length)}</span>
          </div>
          <p className={c.meta}>
            <span>{t('admin.ops.pipeline.pipelineSummary.updatedAt')}{' '}<b>{p.ts}</b>{t('admin.ops.pipeline.pipelineSummary.everySPausedWhileThe', { v: PROGRESS_POLL_MS / 1000 })}</span>
            <span>{t('admin.ops.pipeline.pipelineSummary.runningBatches')}{' '}<b>{running}</b></span>
            <span>{t('admin.ops.pipeline.pipelineSummary.reports')}{' '}<b>{fmtInt(p.db.reports)}</b>{' '}{t('admin.ops.pipeline.pipelineSummary.reports2')}</span>
          </p>
        </div>
        {stale && (
          <p className={c.banner} data-tone="warn" role="alert">
            {t('admin.ops.pipeline.pipelineSummary.theLastRefreshFailedShowing')}
          </p>
        )}
        {items.length > 0 && (
          <ul className={styles.items} aria-label={t('admin.ops.pipeline.pipelineSummary.itemsToHandle')}>
            {items.map(it => (
              <li key={it.text} className={`${c.banner} ${styles.item}`} data-tone={it.level}>
                <Chip tone={it.level}>{it.level === 'bad' ? t('admin.ops.pipeline.pipelineSummary.errors2') : t('admin.ops.pipeline.pipelineSummary.warnings')}</Chip>
                <span className={styles.itemText}>{it.text}</span>
                <a className={c.link} href={`#${SECTION_ID[it.target]}`}>{TARGET_TEXT[it.target]}</a>
              </li>
            ))}
          </ul>
        )}
        {review.length > 0 && (
          <p className={styles.review}>
            <span>{t('admin.ops.pipeline.pipelineSummary.awaitingReview')}</span>
            {review.map(item => <span key={item}>{item}</span>)}
            <Link className={c.link} to="/admin/reviews">{t('admin.ops.pipeline.pipelineSummary.goToReviewQueue')}</Link>
          </p>
        )}
      </div>
    </Card>
  )
}
