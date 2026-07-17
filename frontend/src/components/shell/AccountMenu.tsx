import { useState } from 'react'
import { Link } from 'react-router'
import { Popover } from '../primitives/Popover'
import { Pressable } from '../primitives/Pressable'
import { Icon } from '../primitives/Icon'
import { preloadRoute } from '../../lib/routePreload'
import { useStats } from '../../lib/useStats'
import styles from './AccountMenu.module.css'

export function AccountMenu({ variant }: { variant: 'mini' | 'row' | 'mobile' }) {
  const { data } = useStats()
  const name = data?.username ?? '分析師'
  const [open, setOpen] = useState(false)

  return (
    <div className={styles.wrap}>
      <Pressable
        aria-expanded={open}
        title={name}
        onClick={() => setOpen((o) => !o)}
        className={variant === 'row' ? styles.rowTrigger : styles.avatarBtn}
      >
        <span className={styles.avatar}><Icon name="user" size={17} /></span>
        {variant === 'row' && (
          <span className={styles.rowText}>
            <span className={styles.name}>{name}</span>
            <span className={styles.sub}>研究部 · 分析師</span>
          </span>
        )}
      </Pressable>
      <Popover open={open} onClose={() => setOpen(false)} className={styles.pop} openUp>
        <div className={styles.popHead}>
          <div className={styles.name}>{name}</div>
          <div className={styles.sub}>研究部 · 分析師</div>
        </div>
        <Link
          to="/help"
          className={styles.menuItem}
          onClick={() => setOpen(false)}
          onPointerEnter={() => preloadRoute('help')}
        >
          <Icon name="info" size={16} />使用說明
        </Link>
        <form method="post" action="/logout">
          <Pressable type="submit" className={styles.logout}>登出</Pressable>
        </form>
      </Popover>
    </div>
  )
}
