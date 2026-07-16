import { useLocation } from 'react-router'
import { MotionLink } from '../primitives/MotionLink'
import { Icon, type IconName } from '../primitives/Icon'
import { preloadRoute, type RouteKey } from '../../lib/routePreload'
import styles from './ModeSwitch.module.css'

interface ModeSwitchProps {
  size?: 'md' | 'sm'
  className?: string
}

/** 「檢索／問答」分段控制。每頁僅渲染一個。 */
export function ModeSwitch({ size = 'md', className }: ModeSwitchProps) {
  const askActive = useLocation().pathname.startsWith('/ask')
  const cls = [styles.switch, size === 'sm' ? styles.sm : '', className ?? ''].filter(Boolean).join(' ')
  return (
    <div className={cls} role="group" aria-label="檢索與問答切換">
      <ModeItem to="/search" icon="search" label="檢索" active={!askActive} routeKey="search" />
      <ModeItem to="/ask" icon="messages" label="問答" active={askActive} routeKey="ask" />
    </div>
  )
}

interface ModeItemProps {
  to: string
  icon: IconName
  label: string
  active: boolean
  routeKey: RouteKey
}

function ModeItem({ to, icon, label, active, routeKey }: ModeItemProps) {
  if (active) {
    return (
      <span className={`${styles.mode} ${styles.active}`} aria-current="page">
        <Icon name={icon} size={15} />{label}
      </span>
    )
  }
  const preload = () => preloadRoute(routeKey)
  return (
    <MotionLink
      to={to}
      className={styles.mode}
      onPointerEnter={preload}
      onFocus={preload}
      whileTap={{ scale: 0.96 }}
    >
      <Icon name={icon} size={15} />{label}
    </MotionLink>
  )
}
