import { useRef, useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router'
import { ChevronRight, Info, Shield, SlidersHorizontal, User, X, type LucideIcon } from 'lucide-react'
import { useFluidHover, useRegisterFluidHoverItem } from '@/hooks/use-fluid-hover'
import { FluidHoverHighlight } from '@/components/fluid-hover-highlight'
import { useStableRect } from '@/lib/useStableRect'
import { Modal } from '../../components/primitives/Modal'
import { SystemStatusRow } from '../../components/shell/SystemStatus'
import type { SettingsTab } from '../../components/shell/shellContext'
import { setLocale, useLocale, type Locale } from '../../lib/useLocale'
import { useMe } from '../../lib/useMe'
import { useStats } from '../../lib/useStats'
import { useSystemStatus } from '../../lib/useSystemStatus'
import { SecurityPanel } from './SecurityDialog'
import styles from './SettingsDialog.module.css'

const TABS: { key: SettingsTab; label: string; icon: LucideIcon }[] = [
  { key: 'account', label: '帳號', icon: User },
  { key: 'security', label: '安全', icon: Shield },
  { key: 'prefs', label: '偏好', icon: SlidersHorizontal },
  { key: 'about', label: '關於', icon: Info },
]

const LOCALES: { value: Locale; label: string }[] = [
  { value: 'zh-Hant', label: '中文' },
  { value: 'en', label: 'English' },
]

const ROLE_LABEL: Record<string, string> = { admin: '管理員', user: '使用者' }
const GLIDE = { duration: 0.3, ease: [0.32, 0.72, 0, 1] as const }

/**
 * 設定彈窗（交接包「設定彈窗」）：左側分頁（帳號、安全、偏好、關於），右側內容。
 * 分頁是垂直 tablist：↑↓ Home End 切換。安全分頁就是原本的帳號安全（`SecurityPanel`）；
 * 偏好是回答語言（原本在帳號選單）；關於放系統狀態與使用說明入口。
 */
export function SettingsDialog({ open, tab, onTab, onClose }: {
  open: boolean
  tab: SettingsTab
  onTab: (tab: SettingsTab) => void
  onClose: () => void
}) {
  const [elevating, setElevating] = useState(false)
  const tabsRef = useRef<HTMLDivElement>(null)
  // 只量每個分頁的位置給選取那塊滑動底色；hover 是每個分頁自己的底色（與側欄主導覽一致）。
  const rows = useFluidHover(tabsRef)
  const activeIdx = TABS.findIndex(t => t.key === tab)
  const activeRect = useStableRect(rows, activeIdx)
  const title = TABS[activeIdx]?.label ?? ''

  function onKeyDown(e: KeyboardEvent) {
    const n = TABS.length
    let j = -1
    if (e.key === 'ArrowDown') j = (activeIdx + 1) % n
    else if (e.key === 'ArrowUp') j = (activeIdx - 1 + n) % n
    else if (e.key === 'Home') j = 0
    else if (e.key === 'End') j = n - 1
    if (j < 0) return
    e.preventDefault()
    onTab(TABS[j].key)
    requestAnimationFrame(() => document.getElementById(`settings-tab-${TABS[j].key}`)?.focus())
  }

  return (
    <Modal open={open} onClose={() => { if (!elevating) onClose() }} bare ariaLabel="設定" className={styles.dialog}>
      <div className={styles.layout}>
        <nav className={styles.nav} aria-label="設定分類">
          <h1 className={styles.navTitle}>設定</h1>
          <div ref={tabsRef} className={styles.tabs} role="tablist" aria-orientation="vertical" onKeyDown={onKeyDown}>
            <FluidHoverHighlight rect={activeRect} session={0} fadeIn={false} transition={GLIDE} className={styles.hlActive} />
            {TABS.map((t, i) => (
              <TabButton key={t.key} register={rows.registerItem} index={i} def={t} on={t.key === tab} onPick={() => onTab(t.key)} />
            ))}
          </div>
        </nav>
        <section id="settings-panel" role="tabpanel" aria-labelledby={`settings-tab-${tab}`} className={styles.panel}>
          <header className={styles.panelHead}>
            <h2 className={styles.panelTitle}>{title}</h2>
            <button type="button" className={styles.close} aria-label="關閉" onClick={onClose}>
              <X size={16} strokeWidth={1.8} aria-hidden />
            </button>
          </header>
          <div className={`${styles.body} tf-scroll`} key={tab}>
            {tab === 'account' && <AccountTab />}
            {tab === 'security' && <SecurityPanel active={open} onElevatingChange={setElevating} />}
            {tab === 'prefs' && <PrefsTab />}
            {tab === 'about' && <AboutTab onClose={onClose} />}
          </div>
        </section>
      </div>
    </Modal>
  )
}

function TabButton({ register, index, def, on, onPick }: {
  register: (index: number, element: HTMLElement | null) => void
  index: number
  def: (typeof TABS)[number]
  on: boolean
  onPick: () => void
}) {
  const ref = useRef<HTMLButtonElement>(null)
  useRegisterFluidHoverItem(register, index, ref)
  const Icon = def.icon
  return (
    <button
      ref={ref}
      id={`settings-tab-${def.key}`}
      type="button"
      role="tab"
      aria-selected={on}
      aria-controls="settings-panel"
      tabIndex={on ? 0 : -1}
      className={styles.tab}
      data-active={on || undefined}
      onClick={onPick}
    >
      <Icon size={16} aria-hidden className={styles.tabIcon} />
      {def.label}
    </button>
  )
}

function AccountTab() {
  const me = useMe()
  const { data: stats } = useStats()
  const name = me.data?.username ?? stats?.username ?? '使用者'
  const role = ROLE_LABEL[me.data?.role ?? 'user'] ?? '使用者'
  return (
    <div className={styles.tabIn}>
      <div className={styles.profile}>
        <span className={styles.bigAvatar} aria-hidden>{name.slice(0, 1).toUpperCase()}</span>
        <span className={styles.profileText}><span className={styles.profileName}>{name}</span><span className={styles.muted}>{role}</span></span>
      </div>
      <dl className={styles.rows}>
        <div className={styles.row}><dt>使用者名稱</dt><dd className={styles.num}>{name}</dd></div>
        <div className={styles.row}><dt>角色</dt><dd>{role}</dd></div>
        <div className={styles.row}><dt>密碼</dt><dd className={styles.muted}>由管理員重設</dd></div>
      </dl>
      <form method="post" action="/logout">
        <button type="submit" className={`${styles.btn} ${styles.danger}`}>登出</button>
      </form>
    </div>
  )
}

/** 分段控制：選取的白底小卡是一塊滑動的底色（與側欄、設定分頁的選取同一套），切換時滑到另一格。 */
function PrefsTab() {
  const locale = useLocale()
  const segRef = useRef<HTMLDivElement>(null)
  const seg = useFluidHover(segRef, { axis: 'x' })
  const onRect = useStableRect(seg, LOCALES.findIndex(o => o.value === locale))
  return (
    <div className={styles.tabIn}>
      <div className={styles.row}>
        <span className={styles.rowLabel}>回答語言</span>
        <div ref={segRef} className={styles.seg} role="radiogroup" aria-label="回答語言">
          <FluidHoverHighlight rect={onRect} session={0} fadeIn={false} transition={GLIDE} className={styles.segThumb} />
          {LOCALES.map((o, i) => (
            <SegButton key={o.value} register={seg.registerItem} index={i} label={o.label} on={locale === o.value} onPick={() => setLocale(o.value)} />
          ))}
        </div>
      </div>
    </div>
  )
}

function SegButton({ register, index, label, on, onPick }: {
  register: (index: number, element: HTMLElement | null) => void
  index: number
  label: string
  on: boolean
  onPick: () => void
}) {
  const ref = useRef<HTMLButtonElement>(null)
  useRegisterFluidHoverItem(register, index, ref)
  return (
    <button
      ref={ref}
      type="button"
      role="radio"
      aria-checked={on}
      className={styles.segBtn}
      data-on={on || undefined}
      onClick={onPick}
    >{label}</button>
  )
}

function AboutTab({ onClose }: { onClose: () => void }) {
  const status = useSystemStatus()
  const navigate = useNavigate()
  return (
    <div className={styles.tabIn}>
      <SystemStatusRow status={status} />
      <button type="button" className={styles.linkRow} onClick={() => { onClose(); navigate('/help') }}>
        <span className={styles.rowLabel}>使用說明</span>
        <ChevronRight size={16} strokeWidth={1.5} aria-hidden className={styles.linkChev} />
      </button>
    </div>
  )
}
