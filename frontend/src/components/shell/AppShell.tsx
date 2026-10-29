import { useCallback, useMemo, useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router'
import { motion, useReducedMotion } from 'motion/react'
import { TF_DUR, TF_EASE_OUT } from '../../lib/motionTokens'
import { useMediaQuery } from '../../lib/useMediaQuery'
import { useSidebarCollapsed } from '../../lib/useSidebarCollapsed'
import { useGlobalShortcuts } from '../../lib/useGlobalShortcuts'
import { SettingsDialog } from '../../features/account/SettingsDialog'
import { Sidebar } from './Sidebar'
import { MobileTabBar } from './MobileTabBar'
import { CommandPalette } from './CommandPalette'
import { ToastHost } from './ToastHost'
import { ShellContext, type SettingsTab, type ShellActions } from './shellContext'
import styles from './AppShell.module.css'

export function AppShell() {
  const isMobile = useMediaQuery('(max-width: 767px)')
  const { collapsed, toggle } = useSidebarCollapsed()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const reduced = useReducedMotion()
  const [searchOpen, setSearchOpen] = useState(false)
  const [settings, setSettings] = useState<{ open: boolean; tab: SettingsTab }>({ open: false, tab: 'account' })

  const actions = useMemo<ShellActions>(() => ({
    openSearch: () => setSearchOpen(true),
    openSettings: (tab = 'account') => setSettings({ open: true, tab }),
  }), [])
  const toggleSearch = useCallback(() => setSearchOpen(o => !o), [])
  const newChat = useCallback(() => navigate('/assistant'), [navigate])
  useGlobalShortcuts({ toggleSearch, newChat, pathname })

  // 換頁整頁淡入（Motion 主導；已移除原生 View Transitions，故不再依偵測縮短時長）
  const routeDur = reduced ? 0 : TF_DUR.d2
  return (
    <ShellContext.Provider value={actions}>
      <div className={styles.shell}>
        <h1 className={styles.srOnly}>廷豐智能研報</h1>
        {!isMobile && <Sidebar collapsed={collapsed} onToggle={toggle} />}
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
      <CommandPalette open={searchOpen} onClose={() => setSearchOpen(false)} />
      <SettingsDialog
        open={settings.open}
        tab={settings.tab}
        onTab={tab => setSettings(s => ({ ...s, tab }))}
        onClose={() => setSettings(s => ({ ...s, open: false }))}
      />
      <ToastHost />
    </ShellContext.Provider>
  )
}
