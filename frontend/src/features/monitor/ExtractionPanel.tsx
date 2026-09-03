import styles from './MonitorPage.module.css'
import type { Extraction } from './progressSchema'

/**
 * 抽取品質與回填進度（E1，docs/EXTRACTION_REDESIGN.md §6.5）。
 *
 * 三組訊號，各自回答一個以前只能靠 SQL 或根本回答不了的問題：
 *
 *   回填進度   已達 target_version 的篇數 ÷ 全表。E1d 的深夜回填要跑十幾個晚上，
 *              這條不動就是 timer 沒跑或一直在失敗（後者 unit_failures 會另外亮）。
 *   落點分佈   extraction_log 的 stopped_at：以前「這個檔為什麼不在語料庫」只能去
 *              讀四支腳本的 if 分支（§1.1 的 1,466 筆），現在每個落點有數字。
 *   要人看     needs_review（品質分低於門檻或有頁級失敗）與 pages_failed。**只標記不擋**，
 *              所以這兩個數字大不代表檢索壞了，代表有東西值得有人打開來看。
 *
 * 版本清單刻意列出「(unknown)」：那是 E1b 之前入庫、還沒回填的舊列，回填跑完它會歸零。
 */
const STOPPED_LABEL: Record<string, string> = {
  ingested: '已入庫',
  skip_admin: '行政件',
  scanned: '無文字',
  not_research: '非研報',
  extract_error: '抽取失敗',
}

export function ExtractionPanel({ extraction }: { extraction: Extraction | null | undefined }) {
  if (extraction === undefined) {
    return (
      <div className={`${styles.card} ${styles.panel}`}>
        <div className={styles.ptitle}>抽取品質與回填</div>
        <div className={styles.pidle}>此版後端未提供抽取統計</div>
      </div>
    )
  }
  if (extraction === null) {
    return (
      <div className={`${styles.card} ${styles.panel}`}>
        <div className={styles.ptitle}>抽取品質與回填</div>
        <div className={styles.pidle}>schema 尚未套用（extraction_log 不存在），請執行 make schema</div>
      </div>
    )
  }
  const b = extraction.backfill
  const stoppedEntries = Object.entries(extraction.stopped_at).sort((a, z) => z[1] - a[1])
  return (
    <div className={`${styles.card} ${styles.panel}`}>
      <div className={styles.ptitle}>抽取品質與回填（目標 {extraction.target_version}）</div>
      <div className={styles.fRow}>
        <span className={styles.fLabel}>回填</span>
        <span className={styles.fStats}>
          <span className={styles.fStat}>已達目標 {b.done}/{b.total}</span>
          <span className={styles.fStat}>{b.pct.toFixed(1)}%</span>
          <span className={b.remaining > 0 ? styles.fStat : styles.fMuted}>尚餘 {b.remaining}</span>
        </span>
        <span className={styles.fMuted}>{b.latest ? `最後寫入 ${b.latest}` : '尚無抽取紀錄'}</span>
      </div>
      <div className={styles.fRow}>
        <span className={styles.fLabel}>落點</span>
        <span className={styles.fStats}>
          {stoppedEntries.length === 0 ? (
            <span className={styles.fMuted}>extraction_log 尚無列</span>
          ) : (
            stoppedEntries.map(([k, v]) => (
              <span key={k} className={k === 'extract_error' && v > 0 ? styles.fWarn : styles.fStat}>
                {STOPPED_LABEL[k] ?? k} {v}
              </span>
            ))
          )}
        </span>
      </div>
      <div className={styles.fRow}>
        <span className={styles.fLabel}>要人看</span>
        <span className={styles.fStats}>
          <span className={extraction.needs_review > 0 ? styles.fWarn : styles.fStat}>
            待複核 {extraction.needs_review}
          </span>
          <span className={extraction.pages_failed > 0 ? styles.fWarn : styles.fStat}>
            頁級失敗 {extraction.pages_failed}
          </span>
        </span>
      </div>
      <div className={styles.fRow}>
        <span className={styles.fLabel}>版本</span>
        <span className={styles.fStats}>
          {extraction.versions.map(v => (
            <span key={v.version} className={styles.fStat}>
              {v.version} {v.count}
            </span>
          ))}
        </span>
      </div>
      <div className={styles.prate}>
        品質只標記不擋：待複核與頁級失敗的研報仍可檢索；(unknown) 是尚未回填的舊列
      </div>
    </div>
  )
}
