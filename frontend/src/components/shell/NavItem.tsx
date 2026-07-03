import { Link, useLocation } from 'react-router'
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
    <Link
      to={to}
      title={variant === 'mini' ? label : undefined}
      aria-current={active ? 'page' : undefined}
      className={`${styles[variant]} ${active ? styles.active : ''}`}
    >
      <Icon name={icon} size={variant === 'mobile' ? 21 : variant === 'row' ? 19 : 20} />
      {variant !== 'mini' && <span className={styles.label}>{label}</span>}
    </Link>
  )
}
