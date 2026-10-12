import { Fragment } from 'react'
import type {
  DataHealthResponse, DbAuditSection, FreshnessSection, R2ReconcileSection, ReconcileStats,
} from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError } from './OpsShared'
import OpsRetrievalRegressionCard from './OpsRetrievalRegressionCard'
import { useDataHealth } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

type Status = DataHealthResponse['overall']
type FreshnessState = FreshnessSection['findings'][number]['state']

const STATUS_LABELS: Record<Status, string> = { ok: '正常', warn: '注意', fail: '異常', unknown: '未知' }
const STATUS_CLASS: Record<Status, string> = {
  ok: styles.sRunning, warn: styles.sTransitioning, fail: styles.sFailed, unknown: styles.sIdle,
}
const FRESHNESS_LABELS: Record<FreshnessState, string> = {
  fresh: '新鮮', stale: '停更', suppressed: '略過（無新研報）', disabled: '只列出', upstream_stale: '管線未跑完',
}
const FRESHNESS_CLASS: Record<FreshnessState, string> = {
  fresh: styles.sRunning, stale: styles.sFailed, suppressed: styles.sIdle, disabled: styles.sIdle,
  upstream_stale: styles.sFailed,
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

function StatusPill({ status }: { status: Status }) {
  return <span className={`${styles.pill} ${STATUS_CLASS[status]}`}>{STATUS_LABELS[status]}</span>
}

function fmtAge(days: number | null | undefined): string {
  if (days == null) return '—'
  if (days < 1) return `${(days * 24).toFixed(1)} 小時前`
  return `${days.toFixed(1)} 天前`
}

function ResultMeta({ section, schedule }: { section: DbAuditSection | R2ReconcileSection; schedule: string }) {
  if (!section.available) {
    const why = section.unavailable_reason ?? ''
    return (
      <p className={adminStyles.idle}>
        {UNAVAILABLE[why] ?? `結果檔無法讀取（${why}）`}。{schedule}跑完後才會出現在這裡。
      </p>
    )
  }
  return (
    <>
      <p className={styles.meta}>
        <span>最後一次 <b>{fmtDateTime(section.finished_at)}</b></span>
        {section.exit_code != null && <span>退出碼 <b>{section.exit_code}</b></span>}
        <span>{schedule}</span>
      </p>
      {section.stale && (
        <p className={styles.warnNote}>結果已超過排程週期沒有更新：確認 timer 有在跑、或結果檔寫得進去。</p>
      )}
    </>
  )
}

function FreshnessCard({ s }: { s: FreshnessSection }) {
  return (
    <section className={adminStyles.card} aria-labelledby="dh-fresh-title">
      <div className={adminStyles.cardHead}>
        <h2 id="dh-fresh-title" className={adminStyles.ctitle}>批次新鮮度</h2>
        <StatusPill status={s.status} />
      </div>
      <p className={styles.meta}>
        <span>即時判讀（與每日 08:30 的 report-mark-freshness 同一組規則）</span>
      </p>
      {s.error && <p className={adminStyles.error} role="alert">{s.error}：只能判讀管線心跳。</p>}
      <div className={adminStyles.spacer} />
      <div className={adminStyles.tableWrap}>
        <table className={adminStyles.table} aria-label="批次新鮮度">
          <thead><tr><th>項目</th><th>狀態</th><th>最新產出</th><th>門檻</th><th>說明</th></tr></thead>
          <tbody>
            {s.findings.map(f => (
              <tr key={f.asset}>
                <td>{f.label}</td>
                <td><span className={`${styles.pill} ${FRESHNESS_CLASS[f.state]}`}>{FRESHNESS_LABELS[f.state]}</span></td>
                <td className={adminStyles.num}>
                  {fmtDateTime(f.latest)}
                  {f.age_days != null && <div className={adminStyles.muted}>{fmtAge(f.age_days)}</div>}
                </td>
                <td className={adminStyles.num}>
                  {f.threshold_days > 0 ? `${f.threshold_days} ${f.asset === 'pipeline' ? '小時' : '天'}` : '不告警'}
                </td>
                <td><div className={styles.cellText}>{f.detail}</div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function AuditCard({ s }: { s: DbAuditSection }) {
  return (
    <section className={adminStyles.card} aria-labelledby="dh-audit-title">
      <div className={adminStyles.cardHead}>
        <h2 id="dh-audit-title" className={adminStyles.ctitle}>資料完整性稽核</h2>
        <StatusPill status={s.status} />
      </div>
      <ResultMeta section={s} schedule="每日 08:45 由 report-mark-audit 執行（只讀不修，警告也算失敗）" />
      {s.error && <p className={adminStyles.error} role="alert">{s.error}</p>}
      {s.skipped.length > 0 && <p className={adminStyles.hint}>這次跳過：{s.skipped.join('、')}</p>}
      {s.findings.length > 0 && (
        <>
          <div className={adminStyles.spacer} />
          <div className={adminStyles.tableWrap}>
            <table className={adminStyles.table} aria-label="稽核檢查">
              <thead><tr><th>檢查</th><th>級別</th><th>違反數</th><th>處置</th></tr></thead>
              <tbody>
                {s.findings.map(f => (
                  <tr key={f.key}>
                    <td>
                      <div className={styles.cellText}>{f.label}</div>
                      <div className={adminStyles.muted}>{f.key}</div>
                    </td>
                    <td>{f.severity === 'error' ? '錯誤' : '警告'}</td>
                    <td className={adminStyles.num}>
                      <span className={`${styles.pill} ${f.count > 0 ? styles.sFailed : styles.sRunning}`}>{f.count}</span>
                    </td>
                    <td>
                      {f.count > 0 ? <div className={styles.cellText}>{f.detail}</div> : <span className={adminStyles.muted}>乾淨</span>}
                    </td>
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

function ReconcileCard({ s }: { s: R2ReconcileSection }) {
  return (
    <section className={adminStyles.card} aria-labelledby="dh-r2-title">
      <div className={adminStyles.cardHead}>
        <h2 id="dh-r2-title" className={adminStyles.ctitle}>R2 物件儲存對帳</h2>
        <StatusPill status={s.status} />
      </div>
      <ResultMeta section={s} schedule="每週一 07:00 由 report-mark-r2-reconcile 執行（唯讀，不刪任何物件）" />
      {s.available && s.mode === 'local' && (
        <p className={adminStyles.hint}>OBJECT_STORAGE_MODE=local：沒有 R2 可對帳。</p>
      )}
      {s.stats && (
        <>
          <div className={adminStyles.spacer} />
          <dl className={styles.dl} aria-label="對帳計數">
            {STAT_LABELS.map(([key, label]) => (
              <Fragment key={key}>
                <dt>{label}</dt>
                <dd>{s.stats![key]}</dd>
              </Fragment>
            ))}
            <dt>orphan 掃描</dt>
            <dd>{s.orphan_scan === 'done' ? '已完成' : s.orphan_scan === 'error' ? '失敗' : '略過（有 --limit）'}</dd>
          </dl>
        </>
      )}
      {s.issues.length > 0 && (
        <details className={styles.journal}>
          <summary>
            問題樣本 {s.issues.length} 筆{s.issues_total > s.issues.length && `（共 ${s.issues_total} 筆，完整清單看該次 journal）`}
          </summary>
          <ul className={styles.problems}>
            {s.issues.map((i, n) => (
              <li key={`${i.type}-${i.ref}-${n}`}>
                <span className={`${styles.pill} ${styles.sIdle}`}>{ISSUE_LABELS[i.type] ?? i.type}</span>
                <code>{i.ref}</code>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

/**
 * 資料健康：批次新鮮度（即時）、資料完整性稽核與 R2 對帳（兩支 timer 最後一次的結果檔）。全部唯讀；
 * 稽核與對帳太重，web 不會替你跑——要立刻重跑請到「服務」用「立即執行」（需要 ops.operate）。
 */
export default function OpsDataHealthPage() {
  const q = useDataHealth()
  if (q.isPending) return <p className={adminStyles.idle}>載入中…</p>
  if (q.isError) return <OpsQueryError error={q.error} what="資料健康" />
  const d = q.data
  return (
    <>
      <section className={adminStyles.card} aria-labelledby="dh-title">
        <div className={adminStyles.cardHead}>
          <h2 id="dh-title" className={adminStyles.ctitle}>資料健康</h2>
          <button type="button" className={adminStyles.action} onClick={() => q.refetch()} disabled={q.isFetching}>
            {q.isFetching ? '重新整理中…' : '重新整理'}
          </button>
        </div>
        <p className={styles.meta}>
          <span>整體 <StatusPill status={d.overall} /></span>
          <span>產生時間 <b>{fmtDateTime(d.generated_at)}</b>（伺服器快取 60 秒）</span>
        </p>
      </section>
      <FreshnessCard s={d.freshness} />
      <AuditCard s={d.db_audit} />
      <ReconcileCard s={d.r2_reconcile} />
      <OpsRetrievalRegressionCard />
    </>
  )
}
