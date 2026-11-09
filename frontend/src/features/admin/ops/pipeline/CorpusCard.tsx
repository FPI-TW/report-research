import { Fragment, useState } from 'react'
import { Button, Card } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import { marketColor, marketLabel } from '../../../../lib/meta'
import type { MarketCount, Progress, SourceCount } from '../../../../lib/progressSchema'
import { fmtInt } from './rate'
import styles from './Pipeline.module.css'
import { t } from '@/i18n'

const UNIDENTIFIED = '未辨識'
const BROKER_PREVIEW = 8

function Markets({ markets, reports }: { markets: MarketCount[]; reports: number }) {
  const rows = [...markets].sort((a, b) => b.count - a.count)
  if (rows.length === 0) return <p className={styles.idle}>—</p>
  const max = Math.max(1, ...rows.map(m => m.count))
  return (
    <div className={styles.markets} role="group" aria-label={t('admin.ops.pipeline.corpusCard.marketDistribution')}>
      {rows.map(m => {
        const color = m.market ? marketColor(m.market) : 'var(--mkt-fallback)'
        return (
          <Fragment key={m.market ?? '__none__'}>
            <span className={styles.marketName} title={m.market ?? undefined}>{m.market ? marketLabel(m.market) : t('admin.ops.pipeline.corpusCard.uncategorized')}</span>
            <div className={styles.bar}>
              <div className={styles.barFill} style={{ width: `${((m.count / max) * 100).toFixed(1)}%`, background: color }} />
            </div>
            <span className={`${c.num} ${c.tdRight}`}>
              {fmtInt(m.count)} <span className={c.muted}>{reports ? ((m.count / reports) * 100).toFixed(1) : '0.0'}%</span>
            </span>
          </Fragment>
        )
      })}
    </div>
  )
}

/**
 * 券商分布。三態要分得開：undefined＝舊後端沒這個欄位（降級但不消失）、空陣列＝真的沒有研報、
 * 有列＝正常。中文名由後端對照（`app/services/filename.py` 的 SOURCE_DISPLAY），前端不另存字典；
 * `source` 為 null＝檔名認不出券商，計入篇數但不計入券商家數。「最新一篇」用來看出還有沒有在供稿，
 * 不另設停更門檻。
 */
function Brokers({ sources }: { sources: SourceCount[] | undefined }) {
  const [all, setAll] = useState(false)
  if (sources === undefined) return <p className={styles.idle}>{t('admin.ops.pipeline.corpusCard.thisBackendVersionDoesnT')}</p>
  if (sources.length === 0) return <p className={styles.idle}>—</p>
  const rows = [...sources].sort((a, b) => b.count - a.count)
  const total = rows.reduce((s, r) => s + r.count, 0)
  const named = rows.filter(r => r.source !== null).length
  const unidentified = rows.find(r => r.source === null)?.count ?? 0
  const shown = all ? rows : rows.slice(0, BROKER_PREVIEW)
  return (
    <>
      <p className={styles.brokerSum}>
        {t('admin.ops.pipeline.corpusCard.brokersReports', { named, total: fmtInt(total) })}
        {unidentified > 0 ? t('admin.ops.pipeline.corpusCard.unidentified2', { unidentified: fmtInt(unidentified) }) : ''}
      </p>
      <div className={`${c.tableWrap} ${styles.subTable}`}>
        <table className={c.table} aria-label={t('admin.ops.pipeline.corpusCard.brokerDistribution')}>
          <thead>
            <tr><th>{t('admin.ops.pipeline.corpusCard.broker')}</th><th className={c.tdRight}>{t('admin.ops.pipeline.corpusCard.reports')}</th><th className={c.tdRight}>{t('admin.ops.pipeline.corpusCard.share')}</th><th>{t('admin.ops.pipeline.corpusCard.latestReport')}</th></tr>
          </thead>
          <tbody>
            {shown.map(r => (
              <tr key={r.source ?? '__none__'}>
                {/* title 給原始代碼：中文名對不上時要查得到自己在看哪一個 source */}
                <td title={r.source ?? undefined}>
                  {r.source === null ? <span className={c.muted}>{UNIDENTIFIED}</span> : (r.display || r.source)}
                </td>
                <td className={`${c.num} ${c.tdRight}`}>{fmtInt(r.count)}</td>
                <td className={`${c.num} ${c.tdRight} ${c.muted}`}>
                  {total ? ((r.count / total) * 100).toFixed(1) : '0.0'}%
                </td>
                <td className={c.num}>{r.latest ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > BROKER_PREVIEW && (
        <Button size="sm" className={styles.more} onClick={() => setAll(v => !v)} aria-expanded={all}>
          {all ? t('admin.ops.pipeline.corpusCard.showingOnlyTheFirstRows', { BROKER_PREVIEW }) : t('admin.ops.pipeline.corpusCard.showAllRows', { length: rows.length })}
        </Button>
      )}
    </>
  )
}

export function CorpusCard({ db }: { db: Progress['db'] }) {
  return (
    <Card title={t('admin.ops.pipeline.corpusCard.corpusComposition')} labelledBy="pipeline-corpus-title">
      <div className={styles.body}>
        <div className={styles.kpis}>
          <div><div className={styles.kpiValue}>{fmtInt(db.reports)}</div><div className={styles.kpiLabel}>{t('admin.ops.pipeline.corpusCard.reports2')}</div></div>
          <div><div className={styles.kpiValue}>{fmtInt(db.chunks)}</div><div className={styles.kpiLabel}>{t('admin.ops.pipeline.corpusCard.searchChunks')}</div></div>
        </div>
        <div className={styles.corpus}>
          <div>
            <h3 className={styles.subTitle}>{t('admin.ops.pipeline.corpusCard.marketDistribution')}</h3>
            <Markets markets={db.markets} reports={db.reports} />
          </div>
          <div>
            <h3 className={styles.subTitle}>{t('admin.ops.pipeline.corpusCard.brokerDistribution')}</h3>
            <Brokers sources={db.sources} />
          </div>
        </div>
      </div>
    </Card>
  )
}
