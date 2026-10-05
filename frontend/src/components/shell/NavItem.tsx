import { useLocation } from 'react-router'
import { MotionLink } from '../primitives/MotionLink'
import { Icon, type IconName } from '../primitives/Icon'
import styles from './NavItem.module.css'

interface NavItemProps {
  to: string
  icon: IconName
  label: string
  variant: 'mini' | 'row' | 'mobile'
}

export function NavItem({ to, icon, label, variant }: NavItemProps) {
  const { pathname } = useLocation()
  const active = pathname === to
  return (
    <MotionLink
      to={to}
      title={variant === 'mini' ? label : undefined}
      aria-current={active ? 'page' : undefined}
      data-nav-item=""
      className={`${styles[variant]} ${active ? styles.active : ''}`}
      whileTap={{ scale: variant === 'mobile' ? 0.94 : 0.96 }}
    >
      <Icon name={icon} size={variant === 'mobile' ? 21 : 20} />
      {variant !== 'mini' && <span className={styles.label}>{label}</span>}
    </MotionLink>
  )
}
