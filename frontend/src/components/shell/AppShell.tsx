import { Outlet, useLocation } from 'react-router'
import { motion, useReducedMotion } from 'motion/react'
import { TF_DUR, TF_EASE_OUT } from '../../lib/motionTokens'
import { useMediaQuery } from '../../lib/useMediaQuery'
import { useSidebarCollapsed } from '../../lib/useSidebarCollapsed'
import { SideRail } from './SideRail'
import { MobileTabBar } from './MobileTabBar'
import styles from './AppShell.module.css'

/** 原生 View Transitions 換頁時整頁 crossfade 已由瀏覽器主導，縮短 routeReveal 淡入避免雙重淡入。 */
const supportsViewTransition =
  typeof document !== 'undefined' && typeof document.startViewTransition === 'function'

export function AppShell() {
  const isMobile = useMediaQuery('(max-width: 767px)')
  const { collapsed, toggle } = useSidebarCollapsed()
  const { pathname } = useLocation()
  const reduced = useReducedMotion()
  const routeDur = reduced ? 0 : supportsViewTransition ? TF_DUR.d1 : TF_DUR.d2
  return (
    <div className={styles.shell}>
      <h1 className={styles.srOnly}>廷豐智能研報</h1>
      {!isMobile && <SideRail collapsed={collapsed} onToggle={toggle} />}
      <div className={styles.main}>
        {/* 路由切換時整頁淡入（僅 opacity，不加位移，避免與卡片/每輪進場疊加）；
            key=pathname 讓換頁重播、同頁改 query 不重播 */}
        <motion.div
          key={pathname}
          className={styles.routeReveal}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: routeDur, ease: TF_EASE_OUT }}
        >
          <Outlet />
        </motion.div>
      </div>
      {isMobile && <MobileTabBar />}
    </div>
  )
}
