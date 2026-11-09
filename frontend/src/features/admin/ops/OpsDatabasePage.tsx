import { useState } from 'react'
import { Card, PageHeader, Segmented, StatTile, type SegOption } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { DbOverviewResponse, SectionError, SlowQueryResponse } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError } from './OpsShared'
import { OpsTrendChart } from './OpsTrendChart'
import { type SlowQuerySort, type TrendMetric, useDbOverview, useDbSlowQueries, useDbTrend } from './useDbInsights'
import styles from './data/Data.module.css'
import { t } from '@/i18n'

function fmtBytes(v: number | null | undefined): string {
  if (v == null) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let n = v
  let i = 0
  while (Math.abs(n) >= 1024 && i < units.length - 1) { n /= 1024; i += 1 }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

function fmtPct(v: number | null | undefined, digits = 1): string {
  return v == null ? '—' : `${(v * 100).toFixed(digits)}%`
}

function fmtInt(v: number | null | undefined): string {
  return v == null ? '—' : Math.round(v).toLocaleString('zh-TW')
}

function fmtMs(v: number): string {
  return v >= 1000 ? t('admin.ops.opsDatabasePage.s', { v: (v / 1000).toFixed(1) }) : `${v.toFixed(1)} ms`
}

const STATE_LABELS: Record<string, string> = {
  get active() { return t('admin.ops.opsDatabasePage.active') },
  get idle() { return t('admin.ops.opsDatabasePage.idle') },
  get 'idle in transaction'() { return t('admin.ops.opsDatabasePage.idleInTransaction') },
  get 'idle in transaction (aborted)'() { return t('admin.ops.opsDatabasePage.idleInTransactionAborted') },
  fastpath: 'fastpath',
  get disabled() { return t('admin.ops.opsDatabasePage.notTracked') },
  get unknown() { return t('admin.ops.opsDatabasePage.unknown') },
}

const SLOW_REASON_HINTS: Record<NonNullable<SlowQueryResponse['reason']>, string> = {
  get extension_missing() { return t('admin.ops.opsDatabasePage.thePgStatStatementsExtension') },
  get not_preloaded() { return t('admin.ops.opsDatabasePage.pgStatStatementsExistsBut') },
  get permission_denied() { return t('admin.ops.opsDatabasePage.theCurrentDbAccountIsn') },
  get timeout() { return t('admin.ops.opsDatabasePage.queryTimedOutTryAgain') },
  get error() { return t('admin.ops.opsDatabasePage.queryFailed') },
}

type MetricDef = { value: TrendMetric; label: string; format: (v: number) => string }

const METRICS: MetricDef[] = [
  { value: 'db_size_bytes', get label() { return t('admin.ops.opsDatabasePage.databaseSize') }, format: fmtBytes },
  { value: 'connections_total', get label() { return t('admin.ops.opsDatabasePage.connectionsAll') }, format: fmtInt },
  { value: 'connections_active', get label() { return t('admin.ops.opsDatabasePage.activeConnections') }, format: fmtInt },
  { value: 'connections_idle_in_tx', get label() { return t('admin.ops.opsDatabasePage.idleInTransactionConnections') }, format: fmtInt },
  { value: 'cache_hit_ratio', get label() { return t('admin.ops.opsDatabasePage.cacheHitRatio') }, format: v => fmtPct(v, 2) },
  { value: 'dead_tuple_ratio', get label() { return t('admin.ops.opsDatabasePage.deadTupleRatioAllTables') }, format: v => fmtPct(v, 2) },
  { value: 'temp_bytes', get label() { return t('admin.ops.opsDatabasePage.tempFileWritesHourlyDaily') }, format: fmtBytes },
  { value: 'deadlocks', get label() { return t('admin.ops.opsDatabasePage.deadlocksHourlyDaily') }, format: v => v.toFixed(v < 10 ? 1 : 0) },
  { value: 'table_bytes', get label() { return t('admin.ops.opsDatabasePage.singleTableSize') }, format: fmtBytes },
]

const RANGES: readonly SegOption<string>[] = [
  { value: '1', get label() { return t('admin.ops.opsDatabasePage.1Day') } },
  { value: '7', get label() { return t('admin.ops.opsDatabasePage.7Days') } },
  { value: '30', get label() { return t('admin.ops.opsDatabasePage.30Days') } },
  { value: '90', get label() { return t('admin.ops.opsDatabasePage.90Days') } },
  { value: '365', get label() { return t('admin.ops.opsDatabasePage.1Year') } },
]

const SORTS: readonly SegOption<SlowQuerySort>[] = [
  { value: 'total', get label() { return t('admin.ops.opsDatabasePage.totalTime') } },
  { value: 'mean', get label() { return t('admin.ops.opsDatabasePage.meanTime') } },
  { value: 'calls', get label() { return t('admin.ops.opsDatabasePage.calls') } },
]

/** 位元組的磚：數字與單位分開（「9.0」＋「GiB」）。 */
function bytesStat(v: number | null | undefined): { value: string; unit?: string } {
  if (v == null) return { value: '—' }
  const [n, u] = fmtBytes(v).split(' ')
  return { value: n, unit: u }
}

/** 比例的磚：數字與「%」分開。 */
function pctStat(v: number | null | undefined, digits = 1): { value: string; unit?: string } {
  return v == null ? { value: '—' } : { value: (v * 100).toFixed(digits), unit: '%' }
}

/** 某一段降級（權限不足、逾時）時的說明：只影響那一段，其餘照常。 */
function SectionNote({ error, what }: { error: SectionError | null | undefined; what: string }) {
  if (!error) return null
  return <p className={c.banner} data-tone="warn" role="status">{t('admin.ops.opsDatabasePage.text', { what, message: error.message })}</p>
}

function Snapshot({ ov }: { ov: DbOverviewResponse }) {
  const { database: db, connections: conn, activity: a, tables: item } = ov
  const errors = [
    { e: db.error, what: t('admin.ops.opsDatabasePage.databaseSize') }, { e: conn.error, what: t('admin.ops.opsDatabasePage.connections') }, { e: a.error, what: t('admin.ops.opsDatabasePage.cacheAndTransactionStats') },
  ].filter(x => x.e)
  const connTotal = conn.error ? '—' : fmtInt(conn.total)
  // 沒有值（「—」）的磚不帶單位；降級的那一段不給補充說明。
  const tiles: { name: string; value: string; unit?: string; hint?: string }[] = [
    { name: t('admin.ops.opsDatabasePage.databaseSize'), ...bytesStat(db.size_bytes), hint: db.name ? `${db.name}${t('common.separator')}PostgreSQL ${db.server_version ?? ''}` : undefined },
    { name: t('admin.ops.opsDatabasePage.connections'), value: connTotal, unit: connTotal === '—' ? undefined : `/ ${fmtInt(conn.usable_connections)}`,
      hint: conn.error ? undefined : t('admin.ops.opsDatabasePage.maxReservedUsage', { max_connections: fmtInt(conn.max_connections), reserved_connections: fmtInt(conn.reserved_connections), usage_ratio: fmtPct(conn.usage_ratio) }) },
    { name: t('admin.ops.opsDatabasePage.cacheHitRatio'), ...pctStat(a.cache_hit_ratio, 2),
      hint: a.error ? undefined : t('admin.ops.opsDatabasePage.statsSince', { v: a.stats_reset ? fmtDateTime(a.stats_reset) : t('admin.ops.opsDatabasePage.sinceDatabaseStart') }) },
    { name: t('admin.ops.opsDatabasePage.deadTupleRatio'), ...pctStat(item.dead_ratio, 2),
      hint: item.error ? undefined : t('admin.ops.opsDatabasePage.rowsTables', { dead_tuples: fmtInt(item.dead_tuples), fmtInt: fmtInt((item.live_tuples ?? 0) + (item.dead_tuples ?? 0)), table_count: fmtInt(item.table_count) }) },
    { name: t('admin.ops.opsDatabasePage.tempFileWrites'), ...bytesStat(a.temp_bytes), hint: a.error ? undefined : t('admin.ops.opsDatabasePage.filesCumulative', { temp_files: fmtInt(a.temp_files) }) },
    { name: 'deadlock', value: fmtInt(a.deadlocks), hint: a.error ? undefined : t('admin.ops.opsDatabasePage.rollbackCommit', { xact_rollback: fmtInt(a.xact_rollback), xact_commit: fmtInt(a.xact_commit) }) },
  ]
  return (
    <>
      <div className={`${c.tiles} ${styles.wideTiles}`}>
        {tiles.map(x => (
          <StatTile key={x.name} name={x.name} label={x.name} value={x.value} unit={x.unit} sub={x.hint} subTitle={x.hint} />
        ))}
      </div>
      {errors.map(x => <SectionNote key={x.what} error={x.e} what={x.what} />)}
      {!conn.error && (
        <p className={c.meta} aria-label={t('admin.ops.opsDatabasePage.connectionStates')}>
          {Object.entries(conn.by_state ?? {}).map(([state, n]) => (
            <span key={state}>{STATE_LABELS[state] ?? state} <b>{String(n)}</b></span>
          ))}
          {(conn.hidden ?? 0) > 0 && (
            <span title={t('admin.ops.opsDatabasePage.withoutPgReadAllStats')}>{t('admin.ops.opsDatabasePage.stateHidden')}{' '}<b>{conn.hidden}</b></span>
          )}
          <span>{t('admin.ops.opsDatabasePage.connectedToThisDatabase')}{' '}<b>{fmtInt(conn.this_database)}</b></span>
        </p>
      )}
      <p className={styles.note}>
        {t('admin.ops.opsDatabasePage.snapshotAtEachQueryCapped', { generated_at: fmtDateTime(ov.generated_at), statement_timeout_ms: ov.statement_timeout_ms })}
      </p>
    </>
  )
}

function TablesCard({ ov }: { ov: DbOverviewResponse }) {
  const item = ov.tables
  return (
    <Card
      title={t('admin.ops.opsDatabasePage.tableSizesAndBloatEstimate')}
      labelledBy="ops-db-tables-title"
      hint={item.error ? undefined : t('admin.ops.opsDatabasePage.topBySizeTables', { limit: String(item.limit), table_count: fmtInt(item.table_count) })}
    >
      {item.error ? <div className={styles.body}><SectionNote error={item.error} what={t('admin.ops.opsDatabasePage.tableStats')} /></div> : (
        <>
          <div className={c.tableWrap}>
            <table className={c.table} aria-label={t('admin.ops.opsDatabasePage.perTableStats')}>
              <thead>
                <tr>
                  <th>{t('admin.ops.opsDatabasePage.table')}</th><th className={c.tdRight}>{t('admin.ops.opsDatabasePage.sizeWithIndexes')}</th><th className={c.tdRight}>{t('admin.ops.opsDatabasePage.estimatedRows')}</th>
                  <th className={c.tdRight}>{t('admin.ops.opsDatabasePage.deadRatio')}</th><th>{t('admin.ops.opsDatabasePage.lastAutovacuum')}</th><th>{t('admin.ops.opsDatabasePage.lastAutoanalyze')}</th>
                </tr>
              </thead>
              <tbody>
                {(item.items ?? []).map(it => (
                  <tr key={`${it.schema_name}.${it.table}`}>
                    <td className={c.mono}>
                      <span className={`${c.truncate} ${styles.nameCol}`} title={it.table}>{it.table}</span>
                      <div className={c.sub}>{it.schema_name}</div>
                    </td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtBytes(it.total_bytes)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtInt(it.row_estimate)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtPct(it.dead_ratio)}<div className={c.sub}>{t('admin.ops.opsDatabasePage.rows', { dead_tuples: fmtInt(it.dead_tuples) })}</div></td>
                    <td className={c.num}>{fmtDateTime(it.last_autovacuum ?? it.last_vacuum)}</td>
                    <td className={c.num}>{fmtDateTime(it.last_autoanalyze ?? it.last_analyze)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={`${c.cardFoot} ${styles.footNote}`}>
            <span>{t('admin.ops.opsDatabasePage.bloatIsEstimatedFromDead')}</span>
          </div>
        </>
      )}
    </Card>
  )
}

function UnusedIndexesCard({ ov }: { ov: DbOverviewResponse }) {
  const u = ov.unused_indexes
  const items = u.items ?? []
  return (
    <Card
      title={t('admin.ops.opsDatabasePage.unusedIndexes')}
      labelledBy="ops-db-indexes-title"
      hint={u.error || items.length === 0 ? undefined : t('admin.ops.opsDatabasePage.idxScan0SinceStats')}
    >
      {u.error ? <div className={styles.body}><SectionNote error={u.error} what={t('admin.ops.opsDatabasePage.indexStats')} /></div> : items.length === 0 ? (
        <p className={c.empty}>{t('admin.ops.opsDatabasePage.everyIndexHasBeenUsed')}</p>
      ) : (
        <>
          <div className={c.tableWrap}>
            <table className={c.table} aria-label={t('admin.ops.opsDatabasePage.unusedIndexes')}>
              <thead><tr><th>{t('admin.ops.opsDatabasePage.index')}</th><th>{t('admin.ops.opsDatabasePage.table')}</th><th className={c.tdRight}>{t('admin.ops.opsDatabasePage.size')}</th><th>{t('admin.ops.opsDatabasePage.kind')}</th></tr></thead>
              <tbody>
                {items.map(it => (
                  <tr key={`${it.schema_name}.${it.index}`}>
                    <td className={c.mono}><span className={`${c.truncate} ${styles.nameCol}`} title={it.index}>{it.index}</span></td>
                    <td className={c.mono}>{it.schema_name}.{it.table}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtBytes(it.size_bytes)}</td>
                    <td>{it.is_primary ? t('admin.ops.opsDatabasePage.primaryKey') : it.is_unique ? t('admin.ops.opsDatabasePage.unique') : t('admin.ops.opsDatabasePage.regular')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={`${c.cardFoot} ${styles.footNote}`}>
            <span>{t('admin.ops.opsDatabasePage.total')}{' '}<b>{u.count}</b>{' '}{t('admin.ops.opsDatabasePage.indexes')}<b>{fmtBytes(u.total_bytes)}</b>{t('admin.ops.opsDatabasePage.uniqueAndPrimaryKeyIndexes')}</span>
          </div>
        </>
      )}
    </Card>
  )
}

function SlowQueriesCard() {
  const [sort, setSort] = useState<SlowQuerySort>('total')
  const q = useDbSlowQueries(sort)
  const usable = q.data?.available === true
  return (
    <Card
      title={t('admin.ops.opsDatabasePage.slowQueriesPgStatStatements')}
      labelledBy="ops-db-slow-title"
      right={usable && <Segmented label={t('admin.ops.opsDatabasePage.sort')} options={SORTS} value={sort} onChange={setSort} />}
    >
      {q.isPending ? <p className={c.empty}>{t('admin.ops.opsDatabasePage.loading')}</p> : q.isError ? (
        <div className={styles.body}><OpsQueryError error={q.error} what={t('admin.ops.opsDatabasePage.slowQueries')} /></div>
      ) : !q.data.available ? (
        <div className={c.degraded} role="status">
          <p className={c.banner} data-tone="warn">
            {t('admin.ops.opsDatabasePage.slowQueriesUnavailable', { v: q.data.reason ? SLOW_REASON_HINTS[q.data.reason] : '' })}
          </p>
          {q.data.message && <p className={c.degradedDetail}>{q.data.message}</p>}
          <p>
            {t('admin.ops.opsDatabasePage.enablingIsASeparateMaintenance')}{' '}<code>docs/production_resilience.md</code>
            {t('admin.ops.opsDatabasePage.dbStatsSnapshotsAndSlow')}
          </p>
        </div>
      ) : (
        <>
          <div className={styles.body}>
            <p className={c.meta}>
              <span>{t('admin.ops.opsDatabasePage.statsSince2')}{' '}<b>{q.data.stats_reset ? fmtDateTime(q.data.stats_reset) : t('admin.ops.opsDatabasePage.sinceDatabaseStartOrExtension')}</b></span>
              {(q.data.hidden_count ?? 0) > 0 && <span>{q.data.hidden_count}{' '}{t('admin.ops.opsDatabasePage.queriesHiddenForInsufficientPrivileges')}</span>}
              <span>{t('admin.ops.opsDatabasePage.queryTextUpTo200')}</span>
            </p>
          </div>
          {(q.data.items ?? []).length === 0 ? <p className={c.empty}>{t('admin.ops.opsDatabasePage.noStatsYet')}</p> : (
            <div className={c.tableWrap}>
              <table className={c.table} aria-label={t('admin.ops.opsDatabasePage.slowQueries')}>
                <thead>
                  <tr>
                    <th>{t('admin.ops.opsDatabasePage.query')}</th><th className={c.tdRight}>{t('admin.ops.opsDatabasePage.calls')}</th><th className={c.tdRight}>{t('admin.ops.opsDatabasePage.totalTime2')}</th>
                    <th className={c.tdRight}>{t('admin.ops.opsDatabasePage.mean')}</th><th className={c.tdRight}>{t('admin.ops.opsDatabasePage.rows2')}</th><th className={c.tdRight}>{t('admin.ops.opsDatabasePage.cacheHits')}</th>
                  </tr>
                </thead>
                <tbody>
                  {(q.data.items ?? []).map((it, i) => (
                    <tr key={it.queryid ?? i}>
                      <td>
                        <div className={styles.queryText}>
                          {it.query_hidden ? <span className={c.muted}>{t('admin.ops.opsDatabasePage.hiddenInsufficientPrivileges')}</span> : it.query}
                        </div>
                      </td>
                      <td className={`${c.num} ${c.tdRight}`}>{fmtInt(it.calls)}</td>
                      <td className={`${c.num} ${c.tdRight}`}>{fmtMs(it.total_ms)}</td>
                      <td className={`${c.num} ${c.tdRight}`}>{fmtMs(it.mean_ms)}</td>
                      <td className={`${c.num} ${c.tdRight}`}>{fmtInt(it.rows)}</td>
                      <td className={`${c.num} ${c.tdRight}`}>{fmtPct(it.cache_hit_ratio)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Card>
  )
}

function TrendsCard({ tableOptions }: { tableOptions: string[] }) {
  const [metric, setMetric] = useState<TrendMetric>('db_size_bytes')
  const [days, setDays] = useState('7')
  const [table, setTable] = useState('')
  const q = useDbTrend(metric, Number(days), table)
  const def = METRICS.find(m => m.value === metric) ?? METRICS[0]
  const tables = q.data?.tables.length ? q.data.tables : tableOptions
  return (
    <Card
      title={t('admin.ops.opsDatabasePage.trends')}
      labelledBy="ops-db-trend-title"
      right={<Segmented label={t('admin.ops.opsDatabasePage.range')} options={RANGES} value={days} onChange={setDays} />}
    >
      <div className={styles.body}>
        <div className={styles.controls}>
          <label className={styles.control}>
            {t('admin.ops.opsDatabasePage.metric')}
            <select className={c.select} value={metric} onChange={e => setMetric(e.target.value as TrendMetric)}>
              {METRICS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </label>
          {metric === 'table_bytes' && (
            <label className={styles.control}>
              {t('admin.ops.opsDatabasePage.table')}
              <select className={`${c.select} ${c.inputMono}`} value={table} onChange={e => setTable(e.target.value)}>
                <option value="">{t('admin.ops.opsDatabasePage.chooseATable')}</option>
                {tables.map(item => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
          )}
        </div>
        {metric === 'table_bytes' && table === '' ? (
          <p className={styles.idle}>{t('admin.ops.opsDatabasePage.chooseATableToSee')}</p>
        ) : q.isPending ? <p className={styles.idle}>{t('admin.ops.opsDatabasePage.loading')}</p> : q.isError ? (
          <OpsQueryError error={q.error} what={t('admin.ops.opsDatabasePage.trends')} />
        ) : (
          <>
            <p className={c.meta}>
              <span>{t('admin.ops.opsDatabasePage.granularity')}{' '}<b>{q.data.granularity === 'hour' ? t('admin.ops.opsDatabasePage.hourly') : t('admin.ops.opsDatabasePage.daily')}</b></span>
              {q.data.kind === 'rate' && <span>{q.data.granularity === 'hour' ? t('admin.ops.opsDatabasePage.hourly2') : t('admin.ops.opsDatabasePage.daily')}{t('admin.ops.opsDatabasePage.deltaBlankAtStatsReset')}</span>}
              {q.data.granularity === 'day' && q.data.kind === 'gauge' && <span>{t('admin.ops.opsDatabasePage.dailyAverageTheLightBand')}</span>}
            </p>
            <OpsTrendChart points={q.data.points} label={def.label} format={def.format} />
            {q.data.points.length === 0 && (
              <p className={styles.note}>
                {t('admin.ops.opsDatabasePage.trendsComeFromTheHourly')}
              </p>
            )}
          </>
        )}
      </div>
    </Card>
  )
}

/**
 * 維運 → 資料庫（/app/admin/operations/database；Admin v2 DB lane）。外殼 `OperationsLayout` 已做 ops.read 守門。
 * 即時快照只查系統目錄（後端以短逾時保護、每段獨立降級——權限不足的段落只顯示說明）；慢查詢在 pg_stat_statements
 * 未啟用時顯示原因；趨勢讀每小時的 db_stat_snapshot（30 天內逐時、更早每日）。
 *
 * 趨勢沿用 `OpsTrendChart`（折線＋當日最低～最高的色帶）而不是 console 的 `TrendBars`：資料庫大小這類
 * 「幾乎不動的大數字」從 0 起的長條看不出變化，逐時 30 天有 720 點也排不進長條。
 */
export default function OpsDatabasePage() {
  const q = useDbOverview()
  return (
    <>
      <PageHeader
        summary={t('admin.ops.opsDatabasePage.liveSystemCatalogSnapshotSlow')}
        help={(
          <>
            {t('admin.ops.opsDatabasePage.theLiveSnapshotQueriesOnly')}<code>statement_timeout</code>{t('admin.ops.opsDatabasePage.text2')}<code>lock_timeout</code>{t('admin.ops.opsDatabasePage.refreshingEvery60SEach')}{' '}<code>pg_stat_statements</code>
            {t('admin.ops.opsDatabasePage.neitherCodeNorMigrationsCreate')}{' '}<code>db_stat_snapshot</code>{t('admin.ops.opsDatabasePage.hourlyWithin30DaysDaily')}
          </>
        )}
      />
      {q.isPending ? <p className={c.empty}>{t('admin.ops.opsDatabasePage.loading')}</p> : q.isError ? (
        <OpsQueryError error={q.error} what={t('admin.ops.opsDatabasePage.databaseSnapshot')} />
      ) : <Snapshot ov={q.data} />}
      {q.data && <TablesCard ov={q.data} />}
      <TrendsCard tableOptions={q.data?.tables.error ? [] : (q.data?.tables.items ?? []).slice(0, 20).map(item => `${item.schema_name}.${item.table}`)} />
      <SlowQueriesCard />
      {q.data && <UnusedIndexesCard ov={q.data} />}
    </>
  )
}
