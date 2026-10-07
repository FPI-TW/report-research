import { Link } from 'react-router'
import type { LogEntry, Progress, UnitFailures } from '../../../../lib/progressSchema'
import { SECTION_ID, fmtMinute } from './attention'
import { Bar, Count, Row, TonePill, type Tone } from './parts'
import { PIPELINE_ROWS } from './pipelineMeta'
import { fmtInt, ingestRateText, rateText, type Rates } from './rate'
import adminStyles from '../../Admin.module.css'
import styles from './Pipeline.module.css'

/**
 * 排程與執行：每 3 小時的 NAS 增量同步、哪些批次正在跑、進行中的進度、unit 失敗紀錄。
 *
 * 補的是兩個可觀測性斷層（理由見 docs/production_resilience.md「`unit_failures.log` 的讀取端」）：
 * runtime 區塊原本只認全量腳本的 log，生產實際的入庫路徑 sync 零可見度；`data/unit_failures.log`
 * 在 2026-07-28 停擺時寫了 10 筆卻零消費端。
 */
const SYNC_TONE: Record<string, Tone> = { done: 'ok', running: 'run' }

function SyncRow({ sync }: { sync: LogEntry | null | undefined }) {
  if (sync === undefined) return <Row label="排程同步"><p className={adminStyles.idle}>此版後端未提供同步紀錄</p></Row>
  if (sync === null) return <Row label="排程同步"><p className={adminStyles.idle}>尚無同步紀錄</p></Row>
  return (
    <Row label="排程同步">
      <div className={styles.line}>
        <TonePill tone={SYNC_TONE[sync.status] ?? 'idle'}>{sync.label}</TonePill>
        <span className={adminStyles.num}>{fmtMinute(sync.timestamp)}</span>
        <span className={adminStyles.muted}>每 3 小時一輪</span>
      </div>
      <pre className={styles.raw}>{sync.raw}</pre>
    </Row>
  )
}

/** web 必定在跑（不然看不到這頁），不列。 */
function BatchRow({ pipelines }: { pipelines: Progress['pipelines'] }) {
  const rows = PIPELINE_ROWS.filter(r => r.key !== 'web')
  const on = rows.filter(r => pipelines[r.key])
  const off = rows.filter(r => !pipelines[r.key])
  return (
    <Row label="批次">
      <div className={styles.chips} aria-label="執行中的批次">
        {on.length > 0
          ? on.map(r => <TonePill key={r.key} tone="run">{r.name}・執行中</TonePill>)
          : <TonePill tone="idle">目前沒有批次在跑</TonePill>}
      </div>
      {off.length > 0 && (
        <div className={`${adminStyles.muted} ${styles.small}`}>未執行：{off.map(r => r.name).join('、')}</div>
      )}
    </Row>
  )
}

function ProgressLine({ name, pct, stat, rate }: { name: string; pct: number; stat: string; rate?: string }) {
  return (
    <div className={styles.prog}>
      <span className={styles.progName}>{name}</span>
      <Bar pct={pct} label={`${name}進度`} gold />
      <span className={adminStyles.num}>{`${pct.toFixed(1)}%・${stat}`}</span>
      {rate && <span className={styles.progRate}>{rate}</span>}
    </div>
  )
}

/**
 * 進行中的進度，只在該批次執行中才出現（不常駐空的進度卡）。
 *
 * 匯入要同時看兩條路徑：全量 `ingest_all.py` 有篇數與失敗數（來自 `ingest_run_*.log`），增量
 * `sync_new_reports.py` 沒有進度表徵。全量優先：拿「增量匯入執行中」蓋掉具體數字是資訊量的倒退；
 * 反過來只有增量在跑時，寧可講一句沒有數字的實話，也不要講「無執行中的導入」這句假話。
 * 增量的文案刻意不取 `sync.label`：那塊解析殼層 log，手動直接跑 `.py` 時不會更新。
 */
function ProgressRow({ p, rates }: { p: Progress; rates: Rates }) {
  const lines = []
  if (p.pipelines.ingest) {
    const text = p.orchestrator?.label
      ?? (p.ingest ? `本輪已導入 ${fmtInt(p.ingest.ingested)} 篇・失敗 ${fmtInt(p.ingest.fail)}` : '全量導入執行中')
    lines.push(
      <div key="ingest" className={styles.line}>
        <span className={styles.progName}>全量導入</span><span>{text}</span>
        <span className={`${adminStyles.muted} ${styles.small}`}>{ingestRateText(rates.rpm, rates.cps)}</span>
      </div>,
    )
  } else if (p.pipelines.sync_import) {
    lines.push(
      <div key="sync" className={styles.line}>
        <span className={styles.progName}>增量匯入</span><span>執行中</span>
        <span className={`${adminStyles.muted} ${styles.small}`}>增量匯入沒有進度數字</span>
      </div>,
    )
  }
  if (p.pipelines.tag && p.tagging) {
    const t = p.tagging
    lines.push(
      <ProgressLine key="tag" name="語意標註" pct={t.pct} stat={`${fmtInt(t.done)}/${fmtInt(t.total)}・失敗 ${fmtInt(t.fail)}`}
        rate={rateText(Math.max(0, t.total - t.done), rates.tpm, '篇')} />,
    )
  }
  if (p.pipelines.summaries) {
    const s = p.summary
    lines.push(
      <ProgressLine key="summary" name="摘要生成" pct={s.pct} stat={`尚餘 ${fmtInt(s.remaining)}`}
        rate={rateText(s.remaining, rates.spm, '篇')} />,
    )
  }
  if (lines.length === 0) return null
  return <Row label="進度">{lines}</Row>
}

function FailuresRow({ failures }: { failures: UnitFailures | undefined }) {
  if (!failures) return <Row label="排程失敗"><p className={adminStyles.idle}>此版後端未提供失敗紀錄</p></Row>
  return (
    <Row label="排程失敗">
      <div className={styles.line}>
        <span>近 24 小時 <Count value={failures.count_24h} tone="bad" /> 次</span>
        <span>近 7 日 {fmtInt(failures.count_7d)} 次</span>
        <span className={adminStyles.muted}>最後一次 {fmtMinute(failures.latest)}</span>
      </div>
      {failures.recent.length > 0 && (
        <div className={adminStyles.tableWrap}>
          <table className={adminStyles.table} aria-label="最近的排程失敗">
            <thead><tr><th>時間</th><th>服務</th><th>階段</th><th className={styles.right}>退出碼</th></tr></thead>
            <tbody>
              {failures.recent.map((e, i) => (
                <tr key={`${e.ts ?? 'na'}-${i}`}>
                  <td className={adminStyles.num}>{fmtMinute(e.ts)}</td>
                  <td>{e.unit}</td>
                  <td>{e.stage ?? '—'}</td>
                  <td className={`${adminStyles.num} ${styles.right}`}>
                    {e.rc === null ? <span className={adminStyles.muted}>不明</span> : e.rc}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Row>
  )
}

export function ScheduleCard({ progress: p, rates }: { progress: Progress; rates: Rates }) {
  return (
    <section id={SECTION_ID.schedule} className={adminStyles.card} aria-labelledby="pipeline-schedule-title">
      <div className={adminStyles.cardHead}>
        <h2 id="pipeline-schedule-title" className={adminStyles.ctitle}>排程與執行</h2>
        <Link className={adminStyles.action} to="../jobs" relative="path">看排程工作</Link>
      </div>
      <div className={styles.rows}>
        <SyncRow sync={p.sync} />
        <BatchRow pipelines={p.pipelines} />
        <ProgressRow p={p} rates={rates} />
        <FailuresRow failures={p.unit_failures} />
      </div>
      <p className={adminStyles.hint}>
        摘要、標題、摘錄三段是 best-effort，失敗不會讓 unit 變紅；停更由每日的 report-mark-freshness 偵測後寫進同一份失敗紀錄。
      </p>
    </section>
  )
}
