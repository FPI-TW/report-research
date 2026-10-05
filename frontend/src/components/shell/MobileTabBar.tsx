import { NavItem } from './NavItem'
import { AccountMenu } from './AccountMenu'
import { useIsAdmin } from '../../lib/useMe'
import styles from './MobileTabBar.module.css'

export function MobileTabBar() {
  // 「管理」只對管理員露出；這只是顯示，管理端點本身由後端限管理員。
  const isAdmin = useIsAdmin()
  return (
    <div className={styles.bar}>
      <NavItem to="/search" icon="search" label="檢索" variant="mobile" />
      <NavItem to="/ask" icon="messages" label="問答" variant="mobile" />
      <NavItem to="/radar" icon="compass" label="觀點" variant="mobile" />
      <NavItem to="/brief" icon="fileText" label="簡報" variant="mobile" />
      <NavItem to="/monitor" icon="activity" label="監控" variant="mobile" />
      {isAdmin && <NavItem to="/admin/users" activePrefix="/admin" icon="shield" label="管理" variant="mobile" />}
      <div className={styles.account}><AccountMenu variant="mobile" /></div>
    </div>
  )
}
