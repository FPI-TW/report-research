import { AdminHeader } from './AdminHeader'
import styles from './Admin.module.css'

/**
 * Admin v2 Wave 0 的佔位內容：路由、導覽與權限守門都已接好，頁面本體由各 lane 補上。
 * 不打任何 API（後端對應的 router 也還是空的）。
 */
export function AdminComingSoon({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <AdminHeader title={title} subtitle={subtitle} />
        <section className={styles.card} aria-label={`${title}（建置中）`}>
          <h2 className={styles.ctitle}>建置中</h2>
          <p className={styles.hint}>這一頁正在開發，完成後會在這裡顯示。</p>
        </section>
      </div>
    </div>
  )
}
