import { Outlet, useLocation } from 'react-router'
import { motion, useReducedMotion } from 'motion/react'
import { BrandLogo } from '../BrandLogo'
import { Icon, type IconName } from '../primitives/Icon'
import { MotionLink } from '../primitives/MotionLink'
import { TF_DUR, TF_EASE_OUT } from '../../lib/motionTokens'
import { useMe } from '../../lib/useMe'
import { RequireAdmin } from './RequireAdmin'
import styles from './AdminShell.module.css'

type NavEntry = { to: string; icon: IconName; label: string; scope?: string }

// `scope` 有值的入口只對擁有該 scope 的管理員顯示（顯示層；擋人的是後端 require_scope）。
const NAV: readonly NavEntry[] = [
  { to: '/admin/users', icon: 'user', label: '帳號管理' },
  { to: '/admin/reviews', icon: 'shield', label: '待複核' },
  { to: '/admin/reports', icon: 'fileText', label: '研報管理', scope: 'reports.manage' },
  { to: '/admin/uploads', icon: 'upload', label: '上傳研報', scope: 'reports.manage' },
  { to: '/admin/operations', icon: 'activity', label: '維運', scope: 'ops.read' },
  { to: '/admin/audit', icon: 'clock', label: '操作紀錄' },
]

/**
 * 管理後台（/app/admin/*）的外殼，與研報平台的 `AppShell` 完全分開：沒有研報側欄、
 * 歷史對話與問答入口，只有管理導覽、「回到研報平台」與登出。主平台只在帳號選單留一個
 * 給管理員的入口（`AccountMenu`）。
 *
 * 守門放在外殼這一層（`RequireAdmin` 包住導覽與內容）：非管理員連管理導覽都看不到。
 * **這仍只是顯示層**——授權一律由後端 `web/authz.py` 判，繞過前端也只會拿到 403。
 * 同一套 SPA、同一個 build；只是另一組 layout route（見 `App.tsx`）。
 */
export function AdminShell() {
  const { pathname } = useLocation()
  const reduced = useReducedMotion()
  const me = useMe()
  const scopes = me.data?.scopes ?? []
  const nav = NAV.filter(item => !item.scope || scopes.includes(item.scope))
  return (
    <div className={styles.shell}>
      <header className={styles.topbar}>
        <div className={styles.brand}>
          <BrandLogo size={26} alt="" />
          <span className={styles.brandName}>廷豐智能研報</span>
          <span className={styles.badge}>管理後台</span>
        </div>
        <div className={styles.topActions}>
          {me.data?.username && <span className={styles.who}>{me.data.username}</span>}
          <MotionLink to="/search" className={styles.back} whileTap={{ scale: 0.96 }}>
            <Icon name="arrowLeft" size={16} />回到研報平台
          </MotionLink>
          <form method="post" action="/logout">
            <button type="submit" className={styles.logout}>登出</button>
          </form>
        </div>
      </header>
      <RequireAdmin>
        <div className={styles.body}>
          <nav className={styles.nav} aria-label="管理導覽">
            {nav.map(item => {
              const active = pathname === item.to || pathname.startsWith(item.to + '/')
              return (
                <MotionLink
                  key={item.to}
                  to={item.to}
                  aria-current={active ? 'page' : undefined}
                  className={`${styles.navItem} ${active ? styles.navOn : ''}`}
                  whileTap={{ scale: 0.97 }}
                >
                  <Icon name={item.icon} size={18} />
                  <span>{item.label}</span>
                </MotionLink>
              )
            })}
          </nav>
          <main className={styles.main}>
            <motion.div
              key={pathname}
              className={styles.routeReveal}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: reduced ? 0 : TF_DUR.d2, ease: TF_EASE_OUT }}
            >
              <Outlet />
            </motion.div>
          </main>
        </div>
      </RequireAdmin>
    </div>
  )
}
