import styles from './Admin.module.css'

/** 管理頁的頁首。切頁導覽在外殼（`components/shell/AdminShell.tsx`），這裡只有標題與說明。 */
export function AdminHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <header className={styles.header}>
      <h1 className={styles.title}>{title}</h1>
      <p className={styles.sub}>{subtitle}</p>
    </header>
  )
}
