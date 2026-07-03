import { NavItem } from './NavItem'
import { AccountMenu } from './AccountMenu'
import styles from './MobileTabBar.module.css'

export function MobileTabBar() {
  return (
    <div className={styles.bar}>
      <NavItem to="/search" icon="search" label="檢索" variant="mobile" />
      <NavItem to="/ask" icon="messages" label="問答" variant="mobile" />
      <NavItem to="/monitor" icon="activity" label="監控" variant="mobile" />
      <div className={styles.account}><AccountMenu variant="mobile" /></div>
    </div>
  )
}
