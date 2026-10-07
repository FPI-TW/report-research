import { useState } from 'react'
import type { DbOverviewResponse, SectionError, SlowQueryResponse } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError } from './OpsShared'
import { OpsTrendChart } from './OpsTrendChart'
import { type SlowQuerySort, type TrendMetric, useDbOverview, useDbSlowQueries, useDbTrend } from './useDbInsights'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'
import chartStyles from './OpsTrendChart.module.css'

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
  return v >= 1000 ? `${(v / 1000).toFixed(1)} 秒` : `${v.toFixed(1)} ms`
}

const STATE_LABELS: Record<string, string> = {
  active: '執行中',
  idle: '閒置',
  'idle in transaction': '交易中閒置',
  'idle in transaction (aborted)': '交易中閒置（已中止）',
  fastpath: 'fastpath',
  disabled: '未追蹤',
  unknown: '不明',
}

const SLOW_REASON_HINTS: Record<NonNullable<SlowQueryResponse['reason']>, string> = {
  extension_missing: '尚未建立 pg_stat_statements 擴充。',
  not_preloaded: 'pg_stat_statements 已建立，但 PostgreSQL 沒有預載它（需要改 shared_preload_libraries 並重啟）。',
  permission_denied: '目前的 DB 帳號沒有讀 pg_stat_statements 的權限。',
  timeout: '查詢逾時，稍後再試。',
  error: '查詢失敗。',
}

type MetricDef = { value: TrendMetric; label: string; format: (v: number) => string }

const METRICS: MetricDef[] = [
  { value: 'db_size_bytes', label: '資料庫大小', format: fmtBytes },
  { value: 'connections_total', label: '連線數（全部）', format: fmtInt },
  { value: 'connections_active', label: '執行中連線', format: fmtInt },
  { value: 'connections_idle_in_tx', label: '交易中閒置連線', format: fmtInt },
  { value: 'cache_hit_ratio', label: '快取命中率', format: v => fmtPct(v, 2) },
  { value: 'dead_tuple_ratio', label: 'dead tuple 比例（全部表）', format: v => fmtPct(v, 2) },
  { value: 'temp_bytes', label: '暫存檔寫入量（每小時／每日）', format: fmtBytes },
  { value: 'deadlocks', label: 'deadlock 次數（每小時／每日）', format: v => v.toFixed(v < 10 ? 1 : 0) },
  { value: 'table_bytes', label: '單一表大小', format: fmtBytes },
]

const RANGES = [
  { days: 1, label: '1 天' },
  { days: 7, label: '7 天' },
  { days: 30, label: '30 天' },
  { days: 90, label: '90 天' },
  { days: 365, label: '1 年' },
]

function Stat({ name, value, hint }: { name: string; value: string; hint?: string }) {
  return (
    <div className={styles.tierCard} role="group" aria-label={name}>
      <div className={styles.tierName}>{name}</div>
      <div className={styles.tierCount}>{value}</div>
      {hint && <div className={styles.tierHint}>{hint}</div>}
    </div>
  )
}

function SectionNote({ error, what }: { error: SectionError | null | undefined; what: string }) {
  if (!error) return null
  return <p className={chartStyles.sectionError} role="status">{what}：{error.message}</p>
}

function SnapshotCards({ ov }: { ov: DbOverviewResponse }) {
  const { database: db, connections: c, activity: a, tables: t } = ov
  const errors = [
    { e: db.error, what: '資料庫大小' }, { e: c.error, what: '連線數' }, { e: a.error, what: '快取與交易統計' },
  ].filter(x => x.e)
  return (
    <>
      <div className={styles.tierGrid}>
        <Stat name="資料庫大小" value={fmtBytes(db.size_bytes)} hint={db.name ? `${db.name}・PostgreSQL ${db.server_version ?? ''}` : undefined} />
        <Stat
          name="連線數"
          value={c.error ? '—' : `${fmtInt(c.total)} / ${fmtInt(c.usable_connections)}`}
          hint={c.error ? undefined : `上限 ${fmtInt(c.max_connections)}（保留 ${fmtInt(c.reserved_connections)}）・使用率 ${fmtPct(c.usage_ratio)}`}
        />
        <Stat name="快取命中率" value={fmtPct(a.cache_hit_ratio, 2)} hint={a.error ? undefined : `統計起點 ${a.stats_reset ? fmtDateTime(a.stats_reset) : '資料庫啟動以來'}`} />
        <Stat name="dead tuple 比例" value={fmtPct(t.dead_ratio, 2)} hint={t.error ? undefined : `${fmtInt(t.dead_tuples)} / ${fmtInt((t.live_tuples ?? 0) + (t.dead_tuples ?? 0))} 列（全部 ${fmtInt(t.table_count)} 張表）`} />
        <Stat name="暫存檔寫入" value={fmtBytes(a.temp_bytes)} hint={a.error ? undefined : `${fmtInt(a.temp_files)} 個檔（累計）`} />
        <Stat name="deadlock" value={fmtInt(a.deadlocks)} hint={a.error ? undefined : `rollback ${fmtInt(a.xact_rollback)}／commit ${fmtInt(a.xact_commit)}`} />
      </div>
      {errors.length > 0 && <div className={adminStyles.spacer} />}
      {errors.map(x => <SectionNote key={x.what} error={x.e} what={x.what} />)}
      {!c.error && (
        <>
          <div className={adminStyles.spacer} />
          <p className={styles.meta}>
            {Object.entries(c.by_state ?? {}).map(([state, n]) => (
              <span key={state}>{STATE_LABELS[state] ?? state} <b>{String(n)}</b></span>
            ))}
            {(c.hidden ?? 0) > 0 && (
              <span title="DB 帳號沒有 pg_read_all_stats 時，別人的連線看不到狀態">看不到狀態 <b>{c.hidden}</b></span>
            )}
            <span>連到本庫 <b>{fmtInt(c.this_database)}</b></span>
          </p>
        </>
      )}
    </>
  )
}

function TablesCard({ ov }: { ov: DbOverviewResponse }) {
  const t = ov.tables
  return (
    <section className={adminStyles.card} aria-labelledby="ops-db-tables-title">
      <h2 id="ops-db-tables-title" className={adminStyles.ctitle}>各表大小與膨脹估計</h2>
      {t.error ? <SectionNote error={t.error} what="表統計" /> : (
        <>
          <p className={styles.meta}>
            <span>前 <b>{t.limit}</b> 大（共 {fmtInt(t.table_count)} 張）</span>
            <span>膨脹只用 dead tuple 比例估計（未安裝 pgstattuple）</span>
          </p>
          <div className={adminStyles.spacer} />
          <div className={adminStyles.tableWrap}>
            <table className={adminStyles.table} aria-label="各表統計">
              <thead>
                <tr><th>表</th><th>大小（含索引）</th><th>列數估計</th><th>dead 比例</th><th>最後 autovacuum</th><th>最後 autoanalyze</th></tr>
              </thead>
              <tbody>
                {(t.items ?? []).map(it => (
                  <tr key={`${it.schema_name}.${it.table}`}>
                    <td className={adminStyles.wrapCell}>{it.table}<div className={adminStyles.muted}>{it.schema_name}</div></td>
                    <td className={adminStyles.num}>{fmtBytes(it.total_bytes)}</td>
                    <td className={adminStyles.num}>{fmtInt(it.row_estimate)}</td>
                    <td className={adminStyles.num}>{fmtPct(it.dead_ratio)}<div className={adminStyles.muted}>{fmtInt(it.dead_tuples)} 列</div></td>
                    <td className={adminStyles.num}>{fmtDateTime(it.last_autovacuum ?? it.last_vacuum)}</td>
                    <td className={adminStyles.num}>{fmtDateTime(it.last_autoanalyze ?? it.last_analyze)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

function UnusedIndexesCard({ ov }: { ov: DbOverviewResponse }) {
  const u = ov.unused_indexes
  return (
    <section className={adminStyles.card} aria-labelledby="ops-db-indexes-title">
      <h2 id="ops-db-indexes-title" className={adminStyles.ctitle}>未使用的索引</h2>
      {u.error ? <SectionNote error={u.error} what="索引統計" /> : (u.items ?? []).length === 0 ? (
        <p className={adminStyles.idle}>統計起點以來每個索引都被用過。</p>
      ) : (
        <>
          <p className={styles.meta}>
            <span>共 <b>{u.count}</b> 個、<b>{fmtBytes(u.total_bytes)}</b>（idx_scan = 0，自統計起點）</span>
            <span>唯一／主鍵索引負責約束，沒被掃過也不能刪</span>
          </p>
          <div className={adminStyles.spacer} />
          <div className={adminStyles.tableWrap}>
            <table className={adminStyles.table} aria-label="未使用的索引">
              <thead><tr><th>索引</th><th>表</th><th>大小</th><th>種類</th></tr></thead>
              <tbody>
                {(u.items ?? []).map(it => (
                  <tr key={`${it.schema_name}.${it.index}`}>
                    <td className={adminStyles.wrapCell}>{it.index}</td>
                    <td className={adminStyles.wrapCell}>{it.schema_name}.{it.table}</td>
                    <td className={adminStyles.num}>{fmtBytes(it.size_bytes)}</td>
                    <td>{it.is_primary ? '主鍵' : it.is_unique ? '唯一' : '一般'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

function SlowQueriesCard() {
  const [sort, setSort] = useState<SlowQuerySort>('total')
  const q = useDbSlowQueries(sort)
  return (
    <section className={adminStyles.card} aria-labelledby="ops-db-slow-title">
      <h2 id="ops-db-slow-title" className={adminStyles.ctitle}>慢查詢（pg_stat_statements）</h2>
      {q.isPending ? <p className={adminStyles.idle}>載入中…</p> : q.isError ? (
        <OpsQueryError error={q.error} what="慢查詢" />
      ) : !q.data.available ? (
        <div role="status">
          <p className={chartStyles.sectionError}>
            慢查詢無法使用：{q.data.reason ? SLOW_REASON_HINTS[q.data.reason] : ''}
          </p>
          {q.data.message && <p className={adminStyles.hint}>{q.data.message}</p>}
          <p className={adminStyles.hint}>
            啟用是獨立的維護步驟（網頁不會自動建立擴充或改設定），步驟見 <code>docs/production_resilience.md</code>
            「DB 統計快照與慢查詢」。
          </p>
        </div>
      ) : (
        <>
          <div className={chartStyles.controls}>
            <label className={adminStyles.field}>
              排序
              <select value={sort} onChange={e => setSort(e.target.value as SlowQuerySort)}>
                <option value="total">總執行時間</option>
                <option value="mean">平均執行時間</option>
                <option value="calls">次數</option>
              </select>
            </label>
            <p className={styles.meta}>
              <span>統計起點 <b>{q.data.stats_reset ? fmtDateTime(q.data.stats_reset) : '資料庫啟動或擴充建立以來'}</b></span>
              {(q.data.hidden_count ?? 0) > 0 && <span>{q.data.hidden_count} 筆因權限不足隱藏查詢文字</span>}
              <span>查詢文字最多 200 字</span>
            </p>
          </div>
          {(q.data.items ?? []).length === 0 ? <p className={adminStyles.idle}>還沒有統計資料。</p> : (
            <div className={adminStyles.tableWrap}>
              <table className={adminStyles.table} aria-label="慢查詢">
                <thead><tr><th>查詢</th><th>次數</th><th>總時間</th><th>平均</th><th>列數</th><th>快取命中</th></tr></thead>
                <tbody>
                  {(q.data.items ?? []).map((it, i) => (
                    <tr key={it.queryid ?? i}>
                      <td className={chartStyles.queryText}>
                        {it.query_hidden ? <span className={adminStyles.muted}>（權限不足，已隱藏）</span> : it.query}
                      </td>
                      <td className={adminStyles.num}>{fmtInt(it.calls)}</td>
                      <td className={adminStyles.num}>{fmtMs(it.total_ms)}</td>
                      <td className={adminStyles.num}>{fmtMs(it.mean_ms)}</td>
                      <td className={adminStyles.num}>{fmtInt(it.rows)}</td>
                      <td className={adminStyles.num}>{fmtPct(it.cache_hit_ratio)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  )
}

function TrendsCard({ tableOptions }: { tableOptions: string[] }) {
  const [metric, setMetric] = useState<TrendMetric>('db_size_bytes')
  const [days, setDays] = useState(7)
  const [table, setTable] = useState('')
  const q = useDbTrend(metric, days, table)
  const def = METRICS.find(m => m.value === metric) ?? METRICS[0]
  const tables = q.data?.tables.length ? q.data.tables : tableOptions
  return (
    <section className={adminStyles.card} aria-labelledby="ops-db-trend-title">
      <h2 id="ops-db-trend-title" className={adminStyles.ctitle}>趨勢</h2>
      <div className={chartStyles.controls}>
        <label className={adminStyles.field}>
          指標
          <select value={metric} onChange={e => setMetric(e.target.value as TrendMetric)}>
            {METRICS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </label>
        {metric === 'table_bytes' && (
          <label className={adminStyles.field}>
            表
            <select value={table} onChange={e => setTable(e.target.value)}>
              <option value="">選擇一張表</option>
              {tables.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
        )}
        <label className={adminStyles.field}>
          區間
          <select value={days} onChange={e => setDays(Number(e.target.value))}>
            {RANGES.map(r => <option key={r.days} value={r.days}>{r.label}</option>)}
          </select>
        </label>
      </div>
      {metric === 'table_bytes' && table === '' ? (
        <p className={adminStyles.idle}>選一張表看它的大小變化（選項是最近一次快照記錄的前 20 大表）。</p>
      ) : q.isPending ? <p className={adminStyles.idle}>載入中…</p> : q.isError ? (
        <OpsQueryError error={q.error} what="趨勢" />
      ) : (
        <>
          <p className={styles.meta}>
            <span>粒度 <b>{q.data.granularity === 'hour' ? '逐時' : '每日'}</b></span>
            {q.data.kind === 'rate' && <span>{q.data.granularity === 'hour' ? '每小時' : '每日'}增量（統計重設的那一點留空）</span>}
            {q.data.granularity === 'day' && q.data.kind === 'gauge' && <span>每日平均，淡色帶為當日最低～最高</span>}
          </p>
          <div className={adminStyles.spacer} />
          <OpsTrendChart points={q.data.points} label={def.label} format={def.format} />
          {q.data.points.length === 0 && (
            <p className={adminStyles.hint}>
              趨勢來自每小時的 DB 統計快照（report-mark-db-snapshot.timer）；若一直沒有資料，確認它已安裝並在跑。
            </p>
          )}
        </>
      )}
    </section>
  )
}

/**
 * 維運 → 資料庫（/app/admin/operations/database；Admin v2 DB lane）。外殼 `OperationsLayout` 已做 ops.read 守門。
 * 即時快照只查系統目錄（後端以短逾時保護、每段獨立降級——權限不足的段落只顯示說明）；慢查詢在 pg_stat_statements
 * 未啟用時顯示原因；趨勢讀每小時的 db_stat_snapshot（30 天內逐時、更早每日）。
 */
export default function OpsDatabasePage() {
  const q = useDbOverview()
  return (
    <>
      <section className={adminStyles.card} aria-labelledby="ops-db-title">
        <h2 id="ops-db-title" className={adminStyles.ctitle}>資料庫</h2>
        {q.isPending ? <p className={adminStyles.idle}>載入中…</p> : q.isError ? (
          <OpsQueryError error={q.error} what="資料庫快照" />
        ) : (
          <>
            <SnapshotCards ov={q.data} />
            <p className={adminStyles.hint}>
              快照時間 {fmtDateTime(q.data.generated_at)}・每段查詢上限 {q.data.statement_timeout_ms} ms・每 60 秒更新。
              命中率、暫存檔、deadlock 是統計起點以來的累計值；最近的變化看下方趨勢。
            </p>
          </>
        )}
      </section>
      {q.data && <TablesCard ov={q.data} />}
      {q.data && <UnusedIndexesCard ov={q.data} />}
      <SlowQueriesCard />
      <TrendsCard tableOptions={q.data?.tables.error ? [] : (q.data?.tables.items ?? []).slice(0, 20).map(t => `${t.schema_name}.${t.table}`)} />
    </>
  )
}
