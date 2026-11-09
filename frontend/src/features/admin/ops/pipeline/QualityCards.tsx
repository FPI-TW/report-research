import { Link } from 'react-router'
import { Card, Chip } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import { isNewScale, newScaleText } from '../../../../lib/judgeScale'
import type { EvalSource, Evaluation, Extraction } from '../../../../lib/progressSchema'
import { Bar, Count, Row } from './parts'
import { fmtInt } from './rate'
import styles from './Pipeline.module.css'
import { t } from '@/i18n'

function fmtScore(v: number | null): string {
  return v === null ? '—' : v.toFixed(3)
}

/**
 * 判定尺那一行。主數字「已查核」計**所有 judge**（回答「抽查路徑有沒有在跑」，換 judge 不能驟降），
 * 分數類（fail-open、低於門檻、平均）只計**現行 judge**：兩把尺的分數混著平均沒有意義。所以要說清楚
 * 是哪把尺、該尺查了幾筆、平均的樣本數多大——切換後頭幾天一個 0.5 的平均可能只有一筆。
 * 舊後端沒有量尺欄位時整行不印，不硬湊數字。
 */
function JudgeScale({ d }: { d: EvalSource }) {
  if (!d.judge_model) return null
  const other = d.other_judge_checked ?? 0
  const since = isNewScale(d) ? '' : d.judge_since ? t('admin.ops.pipeline.qualityCards.since', { judge_since: d.judge_since }) : ''
  const counts = [
    d.judge_checked !== undefined ? t('admin.ops.pipeline.qualityCards.checkedByThisJudge', { judge_checked: fmtInt(d.judge_checked) }) : null,
    d.avg_n !== undefined ? t('admin.ops.pipeline.qualityCards.avgSampleSize', { avg_n: fmtInt(d.avg_n) }) : null,
  ].filter(Boolean).join(t('admin.ops.pipeline.qualityCards.text'))
  return (
    <p className={styles.note}>
      {t('admin.ops.pipeline.qualityCards.failOpenBelowThresholdAnd', { judge_model: d.judge_model, since })}
      {counts && t('admin.ops.pipeline.qualityCards.text2', { counts })}
      {other > 0 && t('admin.ops.pipeline.qualityCards.resultsFromOtherJudgesCount', { other: fmtInt(other) })}
      {t('admin.ops.pipeline.qualityCards.failOpenMeansTheJudge')}
    </p>
  )
}

/**
 * M8 忠實度查核。刻意**不畫覆蓋率進度條**：問答端有取樣率、只查含數字的回答，「有查核的比例」
 * 天生就低，做成進度條只會長期亮紅燈。要看的是 fail-open、低於門檻與最後查核有沒有前進。
 */
export function FaithfulnessCard({ evaluation }: { evaluation: Evaluation | undefined }) {
  const qa = evaluation?.qa
  return (
    <Card title={t('admin.ops.pipeline.qualityCards.qAFaithfulness')} hint={t('admin.ops.pipeline.qualityCards.last30Days')} labelledBy="pipeline-faith-title">
      <div className={styles.body}>
        {!evaluation ? (
          <p className={styles.idle}>{t('admin.ops.pipeline.qualityCards.thisBackendVersionDoesnT')}</p>
        ) : !qa ? (
          <p className={styles.idle}>{t('admin.ops.pipeline.qualityCards.noCheckableQAIn')}</p>
        ) : (
          <>
            <div className={styles.rows}>
              <Row label={t('admin.ops.pipeline.qualityCards.checked')}>
                <div className={styles.line}>
                  <span className={c.num} title={t('admin.ops.pipeline.qualityCards.allJudgesCombinedCoverage')}>
                    <b>{fmtInt(qa.checked)}</b> / {fmtInt(qa.total)}
                  </span>
                  <span className={`${c.muted} ${styles.small}`}>{t('admin.ops.pipeline.qualityCards.sampledOnlyAnswersContainingNumbers')}</span>
                </div>
              </Row>
              <Row label="fail-open"><span className={c.num}><Count value={qa.degraded} /></span></Row>
              <Row label={t('admin.ops.pipeline.qualityCards.belowThreshold')}>
                <div className={styles.line}>
                  <span className={c.num}><Count value={qa.below_min} /></span>
                  <span className={c.muted}>{t('admin.ops.pipeline.qualityCards.threshold', { min_score: evaluation.min_score })}</span>
                  <Link className={c.link} to="/admin/reviews">{t('admin.ops.pipeline.qualityCards.goToReviewQueue')}</Link>
                </div>
              </Row>
              <Row label={t('admin.ops.pipeline.qualityCards.averageScore')}>
                <div className={styles.line}>
                  <span className={c.num}><b>{fmtScore(qa.avg_score)}</b></span>
                  {isNewScale(qa) && <Chip tone="gold">{newScaleText(qa)}</Chip>}
                </div>
              </Row>
              <Row label={t('admin.ops.pipeline.qualityCards.lastChecked')}>
                <span className={c.num}>{qa.latest ?? t('admin.ops.pipeline.qualityCards.noChecksYetThreshold', { min_score: evaluation.min_score })}</span>
              </Row>
            </div>
            <JudgeScale d={qa} />
          </>
        )}
      </div>
    </Card>
  )
}

/** extraction_log 的 stopped_at 詞彙（與 `store.STOPPED_AT` 逐字對齊）。未知值原樣顯示，不吞掉。 */
const STOPPED: readonly { key: string; label: string; color: string }[] = [
  { key: 'ingested', get label() { return t('admin.ops.pipeline.qualityCards.ingested') }, color: 'var(--ac-ok)' },
  { key: 'not_research', get label() { return t('admin.ops.pipeline.qualityCards.nonResearch') }, color: 'var(--ac-idle)' },
  { key: 'skip_admin', get label() { return t('admin.ops.pipeline.qualityCards.administrative') }, color: 'var(--ac-l5)' },
  { key: 'scanned', get label() { return t('admin.ops.pipeline.qualityCards.noText') }, color: 'var(--ac-warn)' },
  { key: 'extract_error', get label() { return t('admin.ops.pipeline.qualityCards.extractionFailed') }, color: 'var(--ac-bad)' },
]
const UNKNOWN_STOP_COLOR = 'var(--ac-info)'

function stoppedRows(stoppedAt: Record<string, number>) {
  const known = STOPPED.filter(s => s.key in stoppedAt).map(s => ({ ...s, value: stoppedAt[s.key] }))
  const unknown = Object.entries(stoppedAt)
    .filter(([k]) => !STOPPED.some(s => s.key === k))
    .map(([k, v]) => ({ key: k, label: k, color: UNKNOWN_STOP_COLOR, value: v }))
  return [...known, ...unknown]
}

/**
 * 抽取品質與回填（E1）。回填要跑十幾個晚上，這張卡是不用 SQL 就看得到進度的地方；落點分布回答
 * 「這個檔為什麼不在語料庫」。品質只標記不擋：需複核與頁級失敗的研報仍可檢索。
 * `(unknown)` 版本是尚未回填的舊列，回填跑完會歸零。
 */
export function ExtractionCard({ extraction }: { extraction: Extraction | null | undefined }) {
  if (extraction === undefined || extraction === null) {
    return (
      <Card title={t('admin.ops.pipeline.qualityCards.extractionQualityAndBackfill')} labelledBy="pipeline-extract-title">
        <div className={styles.body}>
          <p className={styles.idle}>
            {extraction === undefined
              ? t('admin.ops.pipeline.qualityCards.thisBackendVersionDoesnT2')
              : t('admin.ops.pipeline.qualityCards.schemaNotAppliedExtractionLog')}
          </p>
        </div>
      </Card>
    )
  }
  const b = extraction.backfill
  const stops = stoppedRows(extraction.stopped_at)
  const stopTotal = stops.reduce((s, r) => s + r.value, 0)
  return (
    <Card title={t('admin.ops.pipeline.qualityCards.extractionQualityAndBackfill')} hint={t('admin.ops.pipeline.qualityCards.target', { target_version: extraction.target_version })} labelledBy="pipeline-extract-title">
      <div className={styles.body}>
        <div className={styles.rows}>
          <Row label={t('admin.ops.pipeline.qualityCards.backfill')}>
            <Bar pct={b.pct} label={t('admin.ops.pipeline.qualityCards.backfillProgress')} gold />
            <div className={styles.line}>
              <span className={c.num}>{b.pct.toFixed(1)}%</span>
              <span className={c.num}>{t('admin.ops.pipeline.qualityCards.at', { target_version: extraction.target_version, done: fmtInt(b.done), total: fmtInt(b.total) })}</span>
              <span className={c.num}>{t('admin.ops.pipeline.qualityCards.remaining', { remaining: fmtInt(b.remaining) })}</span>
              <span className={c.muted}>{b.latest ? t('admin.ops.pipeline.qualityCards.lastWritten', { latest: b.latest }) : t('admin.ops.pipeline.qualityCards.noExtractionRecordsYet')}</span>
            </div>
          </Row>
          <Row label={t('admin.ops.pipeline.qualityCards.outcomes')}>
            {stops.length === 0 ? (
              <span className={c.muted}>{t('admin.ops.pipeline.qualityCards.extractionLogHasNoRows')}</span>
            ) : (
              <>
                <div className={styles.stack} role="img" aria-label={t('admin.ops.pipeline.qualityCards.extractionOutcomeDistribution')}>
                  {stops.map(s => (
                    <span key={s.key} style={{ width: `${stopTotal ? (s.value / stopTotal) * 100 : 0}%`, background: s.color }} />
                  ))}
                </div>
                <div className={styles.legend}>
                  {stops.map(s => (
                    <span key={s.key}>
                      <span className={styles.swatch} style={{ background: s.color }} aria-hidden="true" />
                      {s.label} {s.key === 'extract_error' ? <Count value={s.value} /> : fmtInt(s.value)}
                    </span>
                  ))}
                </div>
              </>
            )}
          </Row>
          <Row label={t('admin.ops.pipeline.qualityCards.needsALook')}>
            <div className={styles.line}>
              <span>{t('admin.ops.pipeline.qualityCards.needsReview')}{' '}<Count value={extraction.needs_review} /></span>
              <span>{t('admin.ops.pipeline.qualityCards.pageLevelFailures')}{' '}<Count value={extraction.pages_failed} /></span>
              <Link className={c.link} to="/admin/reviews">{t('admin.ops.pipeline.qualityCards.goToReviewQueue')}</Link>
            </div>
          </Row>
          <Row label={t('admin.ops.pipeline.qualityCards.version')}>
            <div className={styles.chips}>
              {extraction.versions.map(v => <Chip key={v.version} tone="idle" mono>{v.version} {fmtInt(v.count)}</Chip>)}
            </div>
          </Row>
        </div>
        <p className={styles.note}>{t('admin.ops.pipeline.qualityCards.qualityIsFlaggedNeverBlocking')}</p>
      </div>
    </Card>
  )
}
