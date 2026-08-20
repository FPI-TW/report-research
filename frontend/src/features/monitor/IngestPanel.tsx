import styles from './MonitorPage.module.css'
import { fmtInt } from './rate'
import type { Progress } from './progressSchema'
import { Sweep } from '../../components/primitives/motionLoops'

/**
 * 「報告導入」面板要同時看兩條路徑：全量 `ingest_all.py`（`pipelines.ingest`，只在
 * 初次建庫或補跑歷史時跑）與增量 `sync_new_reports.py`（`pipelines.sync_import`，
 * 生產每 3 小時的實際入庫路徑）。
 *
 * 只認前者的代價是實測踩過的：2026-08-12 手動補積壓時匯入跑了好幾小時，這個面板
 * 全程寫「目前無執行中的導入」——那句話正是使用者回報的症狀。
 *
 * 文案優先序刻意讓**全量贏過增量**：只有全量那條有真實的篇數與失敗數（來自
 * `ingest_run_*.log`），增量沒有進度表徵，拿「增量匯入執行中」蓋掉具體數字是資訊
 * 量的倒退。反過來，增量在跑而全量沒有時，寧可講一句沒有數字的實話，
 * 也不要講「無執行中的導入」這句假話。
 *
 * 增量的文案刻意**不取 `sync.label`**：那塊解析的是殼層 `sync_new_reports.sh` 寫的
 * log，手動直接跑 `.py` 時它不會更新（會停在上一輪的「同步已完成」）。這裡的訊號
 * 來自 `/proc` 掃描，文案就該只反映那件事。
 */
export function IngestPanel({ progress, rateLine }: { progress: Progress; rateLine: string }) {
  const { ingest, orchestrator, pipelines } = progress
  const syncOnly = pipelines.sync_import === true && !pipelines.ingest
  const active = pipelines.ingest || pipelines.sync_import === true
  const current =
    orchestrator?.label ??
    (syncOnly ? '增量匯入執行中' : null) ??
    (ingest ? `本輪已導入 ${fmtInt(ingest.ingested)} 篇 · 失敗 ${fmtInt(ingest.fail)}` : '目前無執行中的導入')
  return (
    <div className={`${styles.card} ${styles.panel}`}>
      <div className={styles.ptitle}>報告導入</div>
      <div className={styles.ingCurrent}>{current}</div>
      {active ? (
        <Sweep className={styles.indetWrap} barClassName={styles.indetBar} duration={1.4} />
      ) : (
        <div className={styles.indetWrap}>
          <div className={styles.indetIdle} />
        </div>
      )}
      <div className={styles.prate}>{rateLine}</div>
    </div>
  )
}
