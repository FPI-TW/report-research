import { NavItem } from './NavItem'
import { AccountMenu } from './AccountMenu'
import styles from './MobileTabBar.module.css'

export function MobileTabBar() {
  return (
    <div className={styles.bar}>
      <NavItem to="/library" icon="search" label="研報庫" />
      <NavItem to="/assistant" icon="messages" label="研究助理" />
      <NavItem to="/consensus" icon="compass" label="券商共識" />
      <NavItem to="/brief" icon="fileText" label="每日簡報" />
      <div className={styles.account}><AccountMenu variant="mobile" /></div>
    </div>
  )
}
