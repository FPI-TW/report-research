import { NavLink } from 'react-router'
import styles from './Admin.module.css'

/** 管理頁的頁首與分頁（帳號／待複核）。兩頁共用，切換時不重新整理外殼。 */
export function AdminHeader({ subtitle }: { subtitle: string }) {
  return (
    <header className={styles.header}>
      <h1 className={styles.title}>管理</h1>
      <p className={styles.sub}>{subtitle}</p>
      <nav className={styles.tabs} aria-label="管理分頁">
        <NavLink to="/admin/users" className={({ isActive }) => `${styles.tab} ${isActive ? styles.tabOn : ''}`}>
          帳號管理
        </NavLink>
        <NavLink to="/admin/reviews" className={({ isActive }) => `${styles.tab} ${isActive ? styles.tabOn : ''}`}>
          待複核
        </NavLink>
      </nav>
    </header>
  )
}
