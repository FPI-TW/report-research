import styles from '../Admin.module.css'

/**
 * 維運 → 資料庫（/app/admin/operations/database；Admin v2 DB lane）。外殼 `OperationsLayout` 已做 ops.read 守門。
 * 後端 /api/admin/db/*：即時快照（系統目錄）與 db_stat_snapshot 趨勢。
 */
export default function OpsDatabasePage() {
  return (
    <section className={styles.card} aria-label="資料庫（建置中）">
      <h2 className={styles.ctitle}>資料庫</h2>
      <p className={styles.hint}>建置中：資料庫大小、各表膨脹估計、未使用的索引、連線數、快取命中率與趨勢。</p>
    </section>
  )
}
