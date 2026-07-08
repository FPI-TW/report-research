import { Outlet, useLocation } from 'react-router'
import { useMediaQuery } from '../../lib/useMediaQuery'
import { useSidebarCollapsed } from '../../lib/useSidebarCollapsed'
import { SideRail } from './SideRail'
import { MobileTabBar } from './MobileTabBar'
import styles from './AppShell.module.css'

export function AppShell() {
  const isMobile = useMediaQuery('(max-width: 767px)')
  const { collapsed, toggle } = useSidebarCollapsed()
  const { pathname } = useLocation()
  return (
    <div className={styles.shell}>
      <h1 className={styles.srOnly}>廷豐智能研報</h1>
      {!isMobile && <SideRail collapsed={collapsed} onToggle={toggle} />}
      <div className={styles.main}>
        {/* 路由切換時整頁淡入（僅 opacity，不加位移，避免與卡片/每輪進場疊加）；
            key=pathname 讓換頁重播、同頁改 query 不重播 */}
        <div key={pathname} className={styles.routeReveal}>
          <Outlet />
        </div>
      </div>
      {isMobile && <MobileTabBar />}
    </div>
  )
}
