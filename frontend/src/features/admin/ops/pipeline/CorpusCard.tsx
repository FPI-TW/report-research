import { Fragment, useState } from 'react'
import { Button, Card } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import { marketColor, marketLabel } from '../../../../lib/meta'
import type { MarketCount, Progress, SourceCount } from '../../../../lib/progressSchema'
import { fmtInt } from './rate'
import styles from './Pipeline.module.css'

const UNIDENTIFIED = '未辨識'
const BROKER_PREVIEW = 8

function Markets({ markets, reports }: { markets: MarketCount[]; reports: number }) {
  const rows = [...markets].sort((a, b) => b.count - a.count)
  if (rows.length === 0) return <p className={styles.idle}>—</p>
  const max = Math.max(1, ...rows.map(m => m.count))
  return (
    <div className={styles.markets} role="group" aria-label="市場分布">
      {rows.map(m => {
        const color = m.market ? marketColor(m.market) : 'var(--mkt-fallback)'
        return (
          <Fragment key={m.market ?? '__none__'}>
            <span className={styles.marketName} title={m.market ?? undefined}>{m.market ? marketLabel(m.market) : '未分類'}</span>
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
  if (sources === undefined) return <p className={styles.idle}>此版後端未提供券商統計</p>
  if (sources.length === 0) return <p className={styles.idle}>—</p>
  const rows = [...sources].sort((a, b) => b.count - a.count)
  const total = rows.reduce((s, r) => s + r.count, 0)
  const named = rows.filter(r => r.source !== null).length
  const unidentified = rows.find(r => r.source === null)?.count ?? 0
  const shown = all ? rows : rows.slice(0, BROKER_PREVIEW)
  return (
    <>
      <p className={styles.brokerSum}>
        {`共 ${named} 家券商・${fmtInt(total)} 篇`}
        {unidentified > 0 ? `（其中未辨識 ${fmtInt(unidentified)} 篇）` : ''}
      </p>
      <div className={`${c.tableWrap} ${styles.subTable}`}>
        <table className={c.table} aria-label="券商分布">
          <thead>
            <tr><th>券商</th><th className={c.tdRight}>篇數</th><th className={c.tdRight}>占比</th><th>最新一篇</th></tr>
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
          {all ? `只顯示前 ${BROKER_PREVIEW} 列` : `顯示全部 ${rows.length} 列`}
        </Button>
      )}
    </>
  )
}

export function CorpusCard({ db }: { db: Progress['db'] }) {
  return (
    <Card title="語料組成" labelledBy="pipeline-corpus-title">
      <div className={styles.body}>
        <div className={styles.kpis}>
          <div><div className={styles.kpiValue}>{fmtInt(db.reports)}</div><div className={styles.kpiLabel}>研報</div></div>
          <div><div className={styles.kpiValue}>{fmtInt(db.chunks)}</div><div className={styles.kpiLabel}>檢索片段</div></div>
        </div>
        <div className={styles.corpus}>
          <div>
            <h3 className={styles.subTitle}>市場分布</h3>
            <Markets markets={db.markets} reports={db.reports} />
          </div>
          <div>
            <h3 className={styles.subTitle}>券商分布</h3>
            <Brokers sources={db.sources} />
          </div>
        </div>
      </div>
    </Card>
  )
}
