import { useState } from 'react'
import { Popover } from '../primitives/Popover'
import { Icon } from '../primitives/Icon'
import { useStats } from '../../lib/useStats'
import styles from './AccountMenu.module.css'

export function AccountMenu({ variant }: { variant: 'mini' | 'row' | 'mobile' }) {
  const { data } = useStats()
  const name = data?.username ?? '分析師'
  const [open, setOpen] = useState(false)

  return (
    <div className={styles.wrap}>
      <button
        type="button"
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
      </button>
      <Popover open={open} onClose={() => setOpen(false)} className={styles.pop}>
        <div className={styles.popHead}>
          <div className={styles.name}>{name}</div>
          <div className={styles.sub}>研究部 · 分析師</div>
        </div>
        <form method="post" action="/logout">
          <button type="submit" className={styles.logout}>登出</button>
        </form>
      </Popover>
    </div>
  )
}
