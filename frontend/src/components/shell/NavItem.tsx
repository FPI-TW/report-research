import { useLocation } from 'react-router'
import { MotionLink } from '../primitives/MotionLink'
import { Icon, type IconName } from '../primitives/Icon'
import styles from './NavItem.module.css'

interface NavItemProps {
  to: string
  icon: IconName
  label: string
}

/** 手機分頁列的一格（桌機側欄的導覽在 Sidebar.tsx）。 */
export function NavItem({ to, icon, label }: NavItemProps) {
  const { pathname } = useLocation()
  const active = pathname === to || pathname.startsWith(`${to}/`) || (to === '/library' && pathname.startsWith('/report/'))
  return (
    <MotionLink
      to={to}
      aria-current={active ? 'page' : undefined}
      data-nav-item=""
      className={`${styles.mobile} ${active ? styles.active : ''}`}
      whileTap={{ scale: 0.94 }}
    >
      <Icon name={icon} size={21} />
      <span className={styles.label}>{label}</span>
    </MotionLink>
  )
}
