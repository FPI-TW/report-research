import { Outlet } from 'react-router'
import { useMediaQuery } from '../../lib/useMediaQuery'
import { useSidebarCollapsed } from '../../lib/useSidebarCollapsed'
import { SideRail } from './SideRail'
import { MobileTabBar } from './MobileTabBar'
import styles from './AppShell.module.css'

export function AppShell() {
  const isMobile = useMediaQuery('(max-width: 767px)')
  const { collapsed, toggle } = useSidebarCollapsed()
  return (
    <div className={styles.shell}>
      <h1 className={styles.srOnly}>廷豐智能研報</h1>
      {!isMobile && <SideRail collapsed={collapsed} onToggle={toggle} />}
      <div className={styles.main}>
        <Outlet />
      </div>
      {isMobile && <MobileTabBar />}
    </div>
  )
}
