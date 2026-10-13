import { Fragment } from 'react'
import { Button, Card, Chip, PageHeader, StatTile, Status, type LampTone } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type {
  DataHealthResponse, DbAuditSection, FreshnessSection, R2ReconcileSection, ReconcileStats,
} from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { HEALTH_LABELS, healthTileTone } from './data/healthLabels'
import { HealthStatus } from './data/HealthStatus'
import { useRunnableServices } from './data/useRunnable'
import { OpsQueryError } from './OpsShared'
import OpsRetrievalRegressionCard from './OpsRetrievalRegressionCard'
import { useServiceActions } from './services/useServiceActions'
import { useDataHealth } from './useOps'
import styles from './data/Data.module.css'

type FreshnessState = FreshnessSection['findings'][number]['state']

const FRESHNESS_LABELS: Record<FreshnessState, string> = {
  fresh: '新鮮', stale: '停更', suppressed: '略過（無新研報）', disabled: '只列出', upstream_stale: '管線未跑完',
}
const FRESHNESS_TONE: Record<FreshnessState, LampTone> = {
  fresh: 'ok', stale: 'bad', suppressed: 'idle', disabled: 'idle', upstream_stale: 'bad',
}
// 與 app/services/data_health.py 的 RECONCILE_FAIL_KEYS／RECONCILE_WARN_KEYS 對應（順序即顯示順序）。
const STAT_LABELS: [keyof ReconcileStats, string][] = [
  ['checked', '已檢查'], ['errors', '錯誤／連不上'], ['unkeyed', 'DB 缺 key'], ['key_mismatch', 'key 與正典不符'],
  ['missing', 'R2 缺檔'], ['size_mismatch', '大小不符'], ['sha_mismatch', 'SHA 不符'],
  ['sha_metadata_missing', '舊檔無 SHA metadata'], ['orphans', 'orphan（R2 有、DB 沒有）'],
]
const ISSUE_LABELS: Record<string, string> = {
  error: '錯誤', unkeyed: 'DB 缺 key', key_mismatch: 'key 不符', missing: '缺檔', size_mismatch: '大小不符',
  sha_mismatch: 'SHA 不符', sha_metadata_missing: '無 SHA metadata', orphan: 'orphan',
}
const UNAVAILABLE: Record<string, string> = { missing: '還沒有結果檔', too_large: '結果檔過大，未讀取' }

/**
 * 頁首的「立即執行」：這三段各自對應的排程服務（Service Catalog 的名稱）。只有這台主機的 catalog 開放 run、
 * 而且有 `ops.operate` 時才顯示；送出前要確認＋重新驗證（`useServiceActions`，和服務頁同一套流程）。
 */
const RUN_SERVICES = ['freshness', 'audit', 'r2-reconcile'] as const

function fmtAge(days: number | null | undefined): string {
  if (days == null) return '—'
  if (days < 1) return `${(days * 24).toFixed(1)} 小時前`
  return `${days.toFixed(1)} 天前`
}

/** 結果檔類的段落在數字磚上的補充：最後一次的時間，或為什麼沒有。 */
function resultHint(section: DbAuditSection | R2ReconcileSection): string {
  if (!section.available) return UNAVAILABLE[section.unavailable_reason ?? ''] ?? '結果檔無法讀取'
  return `最後一次 ${fmtDateTime(section.finished_at)}${section.stale ? '・已過期' : ''}`
}

function ResultMeta({ section, schedule }: { section: DbAuditSection | R2ReconcileSection; schedule: string }) {
  if (!section.available) {
    const why = section.unavailable_reason ?? ''
    return (
      <p className={styles.idle}>
        {UNAVAILABLE[why] ?? `結果檔無法讀取（${why}）`}。{schedule}跑完後才會出現在這裡。
      </p>
    )
  }
  return (
    <>
      <p className={c.meta}>
        <span>最後一次 <b>{fmtDateTime(section.finished_at)}</b></span>
        {section.exit_code != null && <span>退出碼 <b>{section.exit_code}</b></span>}
        <span>{schedule}</span>
      </p>
      {section.stale && (
        <p className={c.banner} data-tone="warn">結果已超過排程週期沒有更新：確認 timer 有在跑、或結果檔寫得進去。</p>
      )}
    </>
  )
}

function FreshnessCard({ s }: { s: FreshnessSection }) {
  return (
    <Card title="批次新鮮度" labelledBy="dh-fresh-title" right={<HealthStatus status={s.status} />}>
      <div className={styles.body}>
        <p className={c.meta}>
          <span>即時判讀（與每日 08:30 的 report-mark-freshness 同一組規則）</span>
        </p>
        {s.error && <p className={c.error} role="alert">{s.error}：只能判讀管線心跳。</p>}
      </div>
      <div className={c.tableWrap}>
        <table className={c.table} aria-label="批次新鮮度">
          <thead><tr><th>項目</th><th>狀態</th><th>最新產出</th><th>門檻</th><th>說明</th></tr></thead>
          <tbody>
            {s.findings.map(f => (
              <tr key={f.asset}>
                <td className={c.nowrap}>{f.label}</td>
                <td><Status tone={FRESHNESS_TONE[f.state]}>{FRESHNESS_LABELS[f.state]}</Status></td>
                <td className={c.num}>
                  {fmtDateTime(f.latest)}
                  {f.age_days != null && <div className={c.sub}>{fmtAge(f.age_days)}</div>}
                </td>
                <td className={c.num}>
                  {f.threshold_days > 0 ? `${f.threshold_days} ${f.asset === 'pipeline' ? '小時' : '天'}` : '不告警'}
                </td>
                <td><div className={styles.cellText}>{f.detail}</div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function AuditCard({ s }: { s: DbAuditSection }) {
  return (
    <Card title="資料完整性稽核" labelledBy="dh-audit-title" right={<HealthStatus status={s.status} />}>
      <div className={styles.body}>
        <ResultMeta section={s} schedule="每日 08:45 由 report-mark-audit 執行（只讀不修，警告也算失敗）" />
        {s.error && <p className={c.error} role="alert">{s.error}</p>}
        {s.skipped.length > 0 && <p className={styles.note}>這次跳過：{s.skipped.join('、')}</p>}
      </div>
      {s.findings.length > 0 && (
        <div className={c.tableWrap}>
          <table className={c.table} aria-label="稽核檢查">
            <thead><tr><th>檢查</th><th>級別</th><th className={c.tdRight}>違反數</th><th>處置</th></tr></thead>
            <tbody>
              {s.findings.map(f => (
                <tr key={f.key}>
                  <td>
                    <div className={styles.cellText}>{f.label}</div>
                    <div className={`${c.sub} ${c.mono}`}>{f.key}</div>
                  </td>
                  <td className={c.nowrap}>{f.severity === 'error' ? '錯誤' : '警告'}</td>
                  <td className={c.tdRight}><Chip tone={f.count > 0 ? 'bad' : 'ok'} mono>{f.count}</Chip></td>
                  <td>
                    {f.count > 0 ? <div className={styles.cellText}>{f.detail}</div> : <span className={c.muted}>乾淨</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function ReconcileCard({ s }: { s: R2ReconcileSection }) {
  return (
    <Card title="R2 物件儲存對帳" labelledBy="dh-r2-title" right={<HealthStatus status={s.status} />}>
      <div className={styles.body}>
        <ResultMeta section={s} schedule="每週一 07:00 由 report-mark-r2-reconcile 執行（唯讀，不刪任何物件）" />
        {s.available && s.mode === 'local' && (
          <p className={styles.note}>OBJECT_STORAGE_MODE=local：沒有 R2 可對帳。</p>
        )}
        {s.stats && (
          <dl className={`${c.kv} ${styles.kvWide}`} aria-label="對帳計數">
            {STAT_LABELS.map(([key, label]) => (
              <Fragment key={key}>
                <dt>{label}</dt>
                <dd>{s.stats![key]}</dd>
              </Fragment>
            ))}
            <dt>orphan 掃描</dt>
            <dd>{s.orphan_scan === 'done' ? '已完成' : s.orphan_scan === 'error' ? '失敗' : '略過（有 --limit）'}</dd>
          </dl>
        )}
        {s.issues.length > 0 && (
          <details className={styles.details}>
            <summary>
              問題樣本 {s.issues.length} 筆{s.issues_total > s.issues.length && `（共 ${s.issues_total} 筆，完整清單看該次 journal）`}
            </summary>
            <ul className={styles.list}>
              {s.issues.map((i, n) => (
                <li key={`${i.type}-${i.ref}-${n}`}>
                  <Chip tone="idle">{ISSUE_LABELS[i.type] ?? i.type}</Chip>
                  <code>{i.ref}</code>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </Card>
  )
}

function Tiles({ d }: { d: DataHealthResponse }) {
  const stale = d.freshness.findings.filter(f => f.state === 'stale' || f.state === 'upstream_stale').length
  const tiles = [
    { name: '整體', status: d.overall, hint: `產生於 ${fmtDateTime(d.generated_at)}・快取 60 秒` },
    { name: '新鮮度', status: d.freshness.status,
      hint: stale > 0 ? `${stale} 項停更・共 ${d.freshness.findings.length} 項` : `即時判讀・共 ${d.freshness.findings.length} 項` },
    { name: '完整性稽核', status: d.db_audit.status, hint: resultHint(d.db_audit) },
    { name: 'R2 對帳', status: d.r2_reconcile.status, hint: resultHint(d.r2_reconcile) },
  ]
  return (
    <div className={`${c.tiles} ${styles.wideTiles}`}>
      {tiles.map(t => (
        <StatTile key={t.name} name={t.name} label={t.name} value={HEALTH_LABELS[t.status]} tone={healthTileTone(t.status)}
          sub={t.hint} subTitle={t.hint} />
      ))}
    </div>
  )
}

/**
 * 資料健康：批次新鮮度（即時）、資料完整性稽核與 R2 對帳（兩支 timer 最後一次的結果檔），下方另有檢索回歸。
 * 讀取全部唯讀；稽核與對帳太重，web 不會替你跑——頁首的「立即執行」是請維運代理觸發對應的排程服務一次
 * （等同 timer 觸發；需要 `ops.operate`、catalog 開放 run，執行前重新驗證），結果檔寫好後這頁才會更新。
 */
export default function OpsDataHealthPage() {
  const q = useDataHealth()
  const ops = useServiceActions()
  const runnable = useRunnableServices(ops.canOperate)
  const header = (
    <PageHeader
      summary="批次新鮮度、資料完整性稽核與 R2 物件儲存對帳。"
      help={(
        <>
          批次新鮮度是即時判讀（與每日 08:30 的 report-mark-freshness 同一組規則）；資料完整性稽核與 R2 對帳不在 web 裡跑，
          讀的是兩支 timer 最後一次寫的結果檔，結果檔比排程週期舊時會標示。檢索回歸同樣只讀結果檔。
          「立即執行」等同 timer 觸發一次，需要 <code>ops.operate</code>，執行前要重新驗證；結果檔寫好後按「重新整理」。
        </>
      )}
      actions={(
        <>
          {RUN_SERVICES.filter(name => runnable.has(name)).map(name => (
            <Button key={name} disabled={ops.busy} onClick={() => ops.request(name, 'run')}>立即執行 {name}</Button>
          ))}
          <Button onClick={() => { void q.refetch() }} disabled={q.isFetching}>
            {q.isFetching ? '重新整理中…' : '重新整理'}
          </Button>
        </>
      )}
    />
  )
  const d = q.data
  return (
    <>
      {header}
      {q.isPending ? <p className={c.empty}>載入中…</p> : q.isError || !d ? (
        <OpsQueryError error={q.error} what="資料健康" />
      ) : (
        <>
          <Tiles d={d} />
          <FreshnessCard s={d.freshness} />
          <AuditCard s={d.db_audit} />
          <ReconcileCard s={d.r2_reconcile} />
          <OpsRetrievalRegressionCard />
        </>
      )}
      {ops.dialogs}
    </>
  )
}
