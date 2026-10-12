import { useState } from 'react'
import { Card, PageHeader, Segmented, type SegOption } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { DbOverviewResponse, SectionError, SlowQueryResponse } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { DataStat } from './data/DataStat'
import { OpsQueryError } from './OpsShared'
import { OpsTrendChart } from './OpsTrendChart'
import { type SlowQuerySort, type TrendMetric, useDbOverview, useDbSlowQueries, useDbTrend } from './useDbInsights'
import styles from './data/Data.module.css'

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

const RANGES: readonly SegOption<string>[] = [
  { value: '1', label: '1 天' },
  { value: '7', label: '7 天' },
  { value: '30', label: '30 天' },
  { value: '90', label: '90 天' },
  { value: '365', label: '1 年' },
]

const SORTS: readonly SegOption<SlowQuerySort>[] = [
  { value: 'total', label: '總執行時間' },
  { value: 'mean', label: '平均執行時間' },
  { value: 'calls', label: '次數' },
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
  return <p className={c.banner} data-tone="warn" role="status">{what}：{error.message}</p>
}

function Snapshot({ ov }: { ov: DbOverviewResponse }) {
  const { database: db, connections: conn, activity: a, tables: t } = ov
  const errors = [
    { e: db.error, what: '資料庫大小' }, { e: conn.error, what: '連線數' }, { e: a.error, what: '快取與交易統計' },
  ].filter(x => x.e)
  return (
    <>
      <div className={`${c.tiles} ${styles.wideTiles}`}>
        <DataStat name="資料庫大小" {...bytesStat(db.size_bytes)} hint={db.name ? `${db.name}・PostgreSQL ${db.server_version ?? ''}` : undefined} />
        <DataStat
          name="連線數"
          value={conn.error ? '—' : fmtInt(conn.total)}
          unit={`/ ${fmtInt(conn.usable_connections)}`}
          hint={conn.error ? undefined : `上限 ${fmtInt(conn.max_connections)}（保留 ${fmtInt(conn.reserved_connections)}）・使用率 ${fmtPct(conn.usage_ratio)}`}
        />
        <DataStat name="快取命中率" {...pctStat(a.cache_hit_ratio, 2)} hint={a.error ? undefined : `統計起點 ${a.stats_reset ? fmtDateTime(a.stats_reset) : '資料庫啟動以來'}`} />
        <DataStat name="dead tuple 比例" {...pctStat(t.dead_ratio, 2)} hint={t.error ? undefined : `${fmtInt(t.dead_tuples)} / ${fmtInt((t.live_tuples ?? 0) + (t.dead_tuples ?? 0))} 列（全部 ${fmtInt(t.table_count)} 張表）`} />
        <DataStat name="暫存檔寫入" {...bytesStat(a.temp_bytes)} hint={a.error ? undefined : `${fmtInt(a.temp_files)} 個檔（累計）`} />
        <DataStat name="deadlock" value={fmtInt(a.deadlocks)} hint={a.error ? undefined : `rollback ${fmtInt(a.xact_rollback)}／commit ${fmtInt(a.xact_commit)}`} />
      </div>
      {errors.map(x => <SectionNote key={x.what} error={x.e} what={x.what} />)}
      {!conn.error && (
        <p className={styles.meta} aria-label="連線狀態">
          {Object.entries(conn.by_state ?? {}).map(([state, n]) => (
            <span key={state}>{STATE_LABELS[state] ?? state} <b>{String(n)}</b></span>
          ))}
          {(conn.hidden ?? 0) > 0 && (
            <span title="DB 帳號沒有 pg_read_all_stats 時，別人的連線看不到狀態">看不到狀態 <b>{conn.hidden}</b></span>
          )}
          <span>連到本庫 <b>{fmtInt(conn.this_database)}</b></span>
        </p>
      )}
      <p className={styles.note}>
        快照時間 {fmtDateTime(ov.generated_at)}・每段查詢上限 {ov.statement_timeout_ms} ms・每 60 秒更新。
        命中率、暫存檔、deadlock 是統計起點以來的累計值；最近的變化看下方趨勢。
      </p>
    </>
  )
}

function TablesCard({ ov }: { ov: DbOverviewResponse }) {
  const t = ov.tables
  return (
    <Card
      title="各表大小與膨脹估計"
      labelledBy="ops-db-tables-title"
      hint={t.error ? undefined : `前 ${t.limit} 大（共 ${fmtInt(t.table_count)} 張）`}
    >
      {t.error ? <div className={styles.body}><SectionNote error={t.error} what="表統計" /></div> : (
        <>
          <div className={c.tableWrap}>
            <table className={c.table} aria-label="各表統計">
              <thead>
                <tr>
                  <th>表</th><th className={c.tdRight}>大小（含索引）</th><th className={c.tdRight}>列數估計</th>
                  <th className={c.tdRight}>dead 比例</th><th>最後 autovacuum</th><th>最後 autoanalyze</th>
                </tr>
              </thead>
              <tbody>
                {(t.items ?? []).map(it => (
                  <tr key={`${it.schema_name}.${it.table}`}>
                    <td className={c.mono}>{it.table}<div className={c.sub}>{it.schema_name}</div></td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtBytes(it.total_bytes)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtInt(it.row_estimate)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtPct(it.dead_ratio)}<div className={c.sub}>{fmtInt(it.dead_tuples)} 列</div></td>
                    <td className={c.num}>{fmtDateTime(it.last_autovacuum ?? it.last_vacuum)}</td>
                    <td className={c.num}>{fmtDateTime(it.last_autoanalyze ?? it.last_analyze)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={`${c.cardFoot} ${styles.footNote}`}>
            <span>膨脹只用 dead tuple 比例估計（未安裝 pgstattuple）</span>
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
      title="未使用的索引"
      labelledBy="ops-db-indexes-title"
      hint={u.error || items.length === 0 ? undefined : 'idx_scan = 0（自統計起點）'}
    >
      {u.error ? <div className={styles.body}><SectionNote error={u.error} what="索引統計" /></div> : items.length === 0 ? (
        <p className={c.empty}>統計起點以來每個索引都被用過。</p>
      ) : (
        <>
          <div className={c.tableWrap}>
            <table className={c.table} aria-label="未使用的索引">
              <thead><tr><th>索引</th><th>表</th><th className={c.tdRight}>大小</th><th>種類</th></tr></thead>
              <tbody>
                {items.map(it => (
                  <tr key={`${it.schema_name}.${it.index}`}>
                    <td className={c.mono}>{it.index}</td>
                    <td className={c.mono}>{it.schema_name}.{it.table}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtBytes(it.size_bytes)}</td>
                    <td>{it.is_primary ? '主鍵' : it.is_unique ? '唯一' : '一般'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={`${c.cardFoot} ${styles.footNote}`}>
            <span>共 <b>{u.count}</b> 個、<b>{fmtBytes(u.total_bytes)}</b>。唯一／主鍵索引負責約束，沒被掃過也不能刪</span>
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
      title="慢查詢（pg_stat_statements）"
      labelledBy="ops-db-slow-title"
      right={usable && <Segmented label="排序" options={SORTS} value={sort} onChange={setSort} />}
    >
      {q.isPending ? <p className={c.empty}>載入中…</p> : q.isError ? (
        <div className={styles.body}><OpsQueryError error={q.error} what="慢查詢" /></div>
      ) : !q.data.available ? (
        <div className={c.degraded} role="status">
          <p className={c.banner} data-tone="warn">
            慢查詢無法使用：{q.data.reason ? SLOW_REASON_HINTS[q.data.reason] : ''}
          </p>
          {q.data.message && <p className={c.degradedDetail}>{q.data.message}</p>}
          <p>
            啟用是獨立的維護步驟（網頁不會自動建立擴充或改設定），步驟見 <code>docs/production_resilience.md</code>
            「DB 統計快照與慢查詢」。
          </p>
        </div>
      ) : (
        <>
          <div className={styles.body}>
            <p className={styles.meta}>
              <span>統計起點 <b>{q.data.stats_reset ? fmtDateTime(q.data.stats_reset) : '資料庫啟動或擴充建立以來'}</b></span>
              {(q.data.hidden_count ?? 0) > 0 && <span>{q.data.hidden_count} 筆因權限不足隱藏查詢文字</span>}
              <span>查詢文字最多 200 字</span>
            </p>
          </div>
          {(q.data.items ?? []).length === 0 ? <p className={c.empty}>還沒有統計資料。</p> : (
            <div className={c.tableWrap}>
              <table className={c.table} aria-label="慢查詢">
                <thead>
                  <tr>
                    <th>查詢</th><th className={c.tdRight}>次數</th><th className={c.tdRight}>總時間</th>
                    <th className={c.tdRight}>平均</th><th className={c.tdRight}>列數</th><th className={c.tdRight}>快取命中</th>
                  </tr>
                </thead>
                <tbody>
                  {(q.data.items ?? []).map((it, i) => (
                    <tr key={it.queryid ?? i}>
                      <td>
                        <div className={styles.queryText}>
                          {it.query_hidden ? <span className={c.muted}>（權限不足，已隱藏）</span> : it.query}
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
      title="趨勢"
      labelledBy="ops-db-trend-title"
      right={<Segmented label="區間" options={RANGES} value={days} onChange={setDays} />}
    >
      <div className={styles.body}>
        <div className={styles.controls}>
          <label className={styles.control}>
            指標
            <select className={c.select} value={metric} onChange={e => setMetric(e.target.value as TrendMetric)}>
              {METRICS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </label>
          {metric === 'table_bytes' && (
            <label className={styles.control}>
              表
              <select className={`${c.select} ${c.inputMono}`} value={table} onChange={e => setTable(e.target.value)}>
                <option value="">選擇一張表</option>
                {tables.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
          )}
        </div>
        {metric === 'table_bytes' && table === '' ? (
          <p className={styles.idle}>選一張表看它的大小變化（選項是最近一次快照記錄的前 20 大表）。</p>
        ) : q.isPending ? <p className={styles.idle}>載入中…</p> : q.isError ? (
          <OpsQueryError error={q.error} what="趨勢" />
        ) : (
          <>
            <p className={styles.meta}>
              <span>粒度 <b>{q.data.granularity === 'hour' ? '逐時' : '每日'}</b></span>
              {q.data.kind === 'rate' && <span>{q.data.granularity === 'hour' ? '每小時' : '每日'}增量（統計重設的那一點留空）</span>}
              {q.data.granularity === 'day' && q.data.kind === 'gauge' && <span>每日平均，淡色帶為當日最低～最高</span>}
            </p>
            <OpsTrendChart points={q.data.points} label={def.label} format={def.format} />
            {q.data.points.length === 0 && (
              <p className={styles.note}>
                趨勢來自每小時的 DB 統計快照（report-mark-db-snapshot.timer）；若一直沒有資料，確認它已安裝並在跑。
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
        summary="系統目錄即時快照、慢查詢與趨勢；只讀系統目錄，不改設定。"
        help={(
          <>
            即時快照只查系統目錄，後端以短逾時保護（<code>statement_timeout</code>、<code>lock_timeout</code>），每 60 秒更新。
            各段獨立降級：權限不足或逾時只讓那一段顯示說明，其餘照常。慢查詢讀 <code>pg_stat_statements</code>
            （程式與 migration 都不建立擴充）；趨勢讀每小時的 <code>db_stat_snapshot</code>（30 天內逐時、更早每日）。
          </>
        )}
      />
      {q.isPending ? <p className={c.empty}>載入中…</p> : q.isError ? (
        <OpsQueryError error={q.error} what="資料庫快照" />
      ) : <Snapshot ov={q.data} />}
      {q.data && <TablesCard ov={q.data} />}
      <TrendsCard tableOptions={q.data?.tables.error ? [] : (q.data?.tables.items ?? []).slice(0, 20).map(t => `${t.schema_name}.${t.table}`)} />
      <SlowQueriesCard />
      {q.data && <UnusedIndexesCard ov={q.data} />}
    </>
  )
}
