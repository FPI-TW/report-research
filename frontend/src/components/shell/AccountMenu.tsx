import { useState } from 'react'
import { Link } from 'react-router'
import { Popover } from '../primitives/Popover'
import { Pressable } from '../primitives/Pressable'
import { Icon } from '../primitives/Icon'
import { SecurityDialog } from '../../features/account/SecurityDialog'
import { preloadRoute } from '../../lib/routePreload'
import { useIsAdmin } from '../../lib/useMe'
import { useStats } from '../../lib/useStats'
import { useLocale, setLocale, type Locale } from '../../lib/useLocale'
import styles from './AccountMenu.module.css'

const LOCALE_OPTIONS: { value: Locale; label: string }[] = [
  { value: 'zh-Hant', label: '中文' },
  { value: 'en', label: 'English' },
]

export function AccountMenu({ variant }: { variant: 'mini' | 'row' | 'mobile' }) {
  const { data } = useStats()
  const name = data?.username ?? '分析師'
  const [open, setOpen] = useState(false)
  const [securityOpen, setSecurityOpen] = useState(false)
  const locale = useLocale()
  // 管理後台的唯一入口：與研報平台分開的外殼（/admin/*），主導覽刻意不放。只是顯示層，授權在後端。
  const isAdmin = useIsAdmin()

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
        <div className={styles.localeRow} role="radiogroup" aria-label="語言 / Language">
          <span className={styles.localeLabel}>語言</span>
          <div className={styles.localeSeg}>
            {LOCALE_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                role="radio"
                aria-checked={locale === o.value}
                className={locale === o.value ? `${styles.localeBtn} ${styles.localeBtnActive}` : styles.localeBtn}
                onClick={() => setLocale(o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>
        <Link
          to="/help"
          className={styles.menuItem}
          onClick={() => setOpen(false)}
          onPointerEnter={() => preloadRoute('help')}
        >
          <Icon name="info" size={16} />使用說明
        </Link>
        <button
          type="button"
          className={styles.menuItem}
          onClick={() => { setOpen(false); setSecurityOpen(true) }}
        >
          <Icon name="shield" size={16} />帳號安全
        </button>
        {isAdmin && (
          <Link
            to="/admin/users"
            className={styles.menuItem}
            onClick={() => setOpen(false)}
            onPointerEnter={() => { preloadRoute('adminShell'); preloadRoute('adminUsers') }}
          >
            <Icon name="shield" size={16} />管理後台
          </Link>
        )}
        <form method="post" action="/logout">
          <Pressable type="submit" className={styles.logout}>登出</Pressable>
        </form>
      </Popover>
      <SecurityDialog open={securityOpen} onClose={() => setSecurityOpen(false)} />
    </div>
  )
}
