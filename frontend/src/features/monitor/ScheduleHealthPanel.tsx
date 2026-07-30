import styles from './MonitorPage.module.css'
import type { LogEntry, UnitFailures } from './progressSchema'

/**
 * 排程健康：每 3 小時的 NAS 增量同步 + unit 失敗告警。
 *
 * 這張卡補的是兩個獨立的可觀測性斷層：
 *
 * 1. **runtime 區塊只認 `tag_run_*` / `ingest_run_*` 兩種 log**，而那兩支全量腳本
 *    只在初次建庫或補跑歷史時才跑。生產實際的入庫路徑是 `sync_new_reports.sh`
 *    （systemd timer，每 3 小時），它在監控頁上一直是零可見度。
 * 2. **`data/unit_failures.log` 零程式消費端**。2026-07-28 那次 24 小時停擺，
 *    `OnFailure` 確實寫進了 10 筆告警、webhook 也沒設，於是整整一天沒有人知道。
 *
 * 紅點條件刻意是「近 24 小時有失敗」而不是「檔案裡有失敗」：後者上線第一天就永遠
 * 亮著，兩週內會被當成背景噪音（本專案已有「永遠紅的東西會被停用」的教訓）。
 */
function fmtTs(ts: string | null): string {
  if (!ts) return '—'
  // 後端給的是 ISO-8601（`date -Iseconds`，含時區偏移）。只取到分，秒沒有意義。
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(ts)
  return m ? `${m[1]} ${m[2]}` : ts
}

function failureText(e: { unit: string; stage: string | null; rc: number | null }): string {
  const parts = [e.unit]
  if (e.stage) parts.push(e.stage)
  if (e.rc !== null) parts.push(`rc=${e.rc}`)
  return parts.join(' · ')
}

export function ScheduleHealthPanel({
  sync,
  failures,
}: {
  sync: LogEntry | null | undefined
  failures: UnitFailures | undefined
}) {
  const alerting = (failures?.count_24h ?? 0) > 0
  return (
    <div className={`${styles.card} ${styles.panel}`}>
      <div className={styles.ptitle}>
        排程健康
        {alerting ? <span className={styles.alertDot} aria-label="近 24 小時有排程失敗" /> : null}
      </div>

      <div className={styles.fRow}>
        <span className={styles.fLabel}>同步</span>
        {sync ? (
          <>
            <span className={styles.fStats}>
              <span className={styles.fStat}>{sync.label}</span>
              <span className={styles.fStat}>{sync.timestamp ?? '—'}</span>
            </span>
            <span className={styles.fMuted}>{sync.raw}</span>
          </>
        ) : (
          <span className={styles.fStat}>尚無同步紀錄</span>
        )}
      </div>

      <div className={styles.fRow}>
        <span className={styles.fLabel}>失敗</span>
        {failures ? (
          <>
            <span className={styles.fStats}>
              <span className={alerting ? styles.fWarn : styles.fStat}>
                近 24 小時 {failures.count_24h} 筆
              </span>
              <span className={styles.fStat}>近 7 日 {failures.count_7d} 筆</span>
            </span>
            <span className={styles.fMuted}>最後失敗 {fmtTs(failures.latest)}</span>
          </>
        ) : (
          <span className={styles.fStat}>此版後端未提供失敗紀錄</span>
        )}
      </div>

      {failures && failures.recent.length > 0 ? (
        <div className={styles.failList}>
          {failures.recent.map((e, i) => (
            <div key={`${e.ts ?? 'na'}-${i}`} className={styles.failRow}>
              <span className={styles.failTs}>{fmtTs(e.ts)}</span>
              <span className={styles.failWhat}>{failureText(e)}</span>
            </div>
          ))}
        </div>
      ) : null}

      <div className={styles.prate}>
        摘要／標題／摘錄三段是 best-effort（失敗不會讓 unit 變紅），停更靠每日的
        report-mark-freshness.timer 偵測後寫進同一份紀錄
      </div>
    </div>
  )
}
