import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Link } from 'react-router'
import { Popover } from '../primitives/Popover'
import { Pressable } from '../primitives/Pressable'
import { Icon } from '../primitives/Icon'
import { preloadRoute } from '../../lib/routePreload'
import { useIsAdmin } from '../../lib/useMe'
import { useStats } from '../../lib/useStats'
import { useLocale, setLocale, type Locale } from '../../lib/useLocale'
import { floatingPlacement, type AccountMenuVariant } from './accountMenuPlacement'
import styles from './AccountMenu.module.css'

const LOCALE_OPTIONS: { value: Locale; label: string }[] = [
  { value: 'zh-Hant', label: '中文' },
  { value: 'en', label: 'English' },
]

export function AccountMenu({ variant }: { variant: AccountMenuVariant }) {
  const { data } = useStats()
  const name = data?.username ?? '分析師'
  const [open, setOpen] = useState(false)
  const locale = useLocale()
  // 管理後台的唯一入口：與研報平台分開的外殼（/admin/*），主導覽刻意不放。只是顯示層，授權在後端。
  const isAdmin = useIsAdmin()
  const wrapRef = useRef<HTMLDivElement>(null)
  const floating = variant !== 'row'
  const [placement, setPlacement] = useState<CSSProperties | undefined>(undefined)

  // 開啟期間逐幀追蹤觸發點的位置（只有 mini／mobile 需要；row 走 CSS）。不能只在開啟那一刻算一次：
  // 剛收合側欄、頁面還在載入時版面仍在位移，量到的是舊位置，選單會浮在半空中。位置沒變就不 setState。
  useEffect(() => {
    if (!open || !floating) return
    let frame = 0
    let last = ''
    const track = () => {
      const el = wrapRef.current
      if (el) {
        const r = el.getBoundingClientRect()
        const key = `${r.top},${r.right},${r.bottom},${window.innerWidth},${window.innerHeight}`
        if (key !== last) {
          last = key
          setPlacement(floatingPlacement(variant, r, window.innerWidth, window.innerHeight))
        }
      }
      frame = requestAnimationFrame(track)
    }
    track()
    return () => cancelAnimationFrame(frame)
  }, [open, floating, variant])

  return (
    <div className={styles.wrap} ref={wrapRef}>
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
      <Popover
        open={open}
        onClose={() => setOpen(false)}
        className={floating ? styles.popFloat : styles.pop}
        openUp
        portal={floating}
        style={floating ? placement : undefined}
      >
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
    </div>
  )
}
