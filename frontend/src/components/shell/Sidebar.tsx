import { useRef, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router'
import { BarChart3, CircleHelp, FileText, Library, MessageSquare, PanelLeft, Search, type LucideIcon } from 'lucide-react'
import { useFluidHover, useRegisterFluidHoverItem } from '@/hooks/use-fluid-hover'
import { FluidHoverHighlight } from '@/components/fluid-hover-highlight'
import { useStableRect } from '@/lib/useStableRect'
import { Tooltip } from '@/components/ui/tooltip'
import { BrandLogo } from '../BrandLogo'
import { preloadRoute, type RouteKey } from '../../lib/routePreload'
import { useIsMac } from '../../lib/useIsMac'
import { ConversationList } from './ConversationList'
import { AccountMenu } from './AccountMenu'
import { useShell } from './shellContext'
import styles from './Sidebar.module.css'

interface NavDef {
  to: string
  label: string
  icon: LucideIcon
  preload: RouteKey
  /** 這些路徑也算在這一項底下（閱讀頁是從研報庫進去的詳情頁）。 */
  also?: string[]
}

const NAV: NavDef[] = [
  { to: '/library', label: '研報庫', icon: Library, preload: 'search', also: ['/report/'] },
  { to: '/assistant', label: '研究助理', icon: MessageSquare, preload: 'ask' },
  { to: '/consensus', label: '券商共識', icon: BarChart3, preload: 'radar' },
  { to: '/brief', label: '每日簡報', icon: FileText, preload: 'brief' },
]

/** 滑動高亮的過渡：Apple 式減速曲線，與交接包的 0.3s 一致。 */
const GLIDE = { duration: 0.3, ease: [0.32, 0.72, 0, 1] as const }

function activeIndexOf(pathname: string): number {
  // 第 0 列是「搜尋」（開彈窗，不是頁面），頁面從第 1 列起算。
  const i = NAV.findIndex(n => pathname === n.to || pathname.startsWith(`${n.to}/`) || n.also?.some(p => pathname.startsWith(p)))
  return i < 0 ? -1 : i + 1
}

type Register = (index: number, element: HTMLElement | null) => void

/** 一列只登錄一次（Fluid 的 useRegisterFluidHoverItem）。行內 ref callback 每次重繪都會
 *  先解除再重新登錄，滑動高亮的量測會一直被重置、底色永遠出不來。 */
function SearchRow({ register, kbd, mac, collapsed, onClick }: { register: Register; kbd: string; mac: boolean; collapsed: boolean; onClick: () => void }) {
  const ref = useRef<HTMLButtonElement>(null)
  useRegisterFluidHoverItem(register, 0, ref)
  return (
    <RowTip show={collapsed} label={<span className={styles.tipKbd}>搜尋<kbd>{mac ? '⌘' : 'Ctrl'}</kbd><kbd>K</kbd></span>}>
      <button ref={ref} type="button" className={styles.row} aria-haspopup="dialog" onClick={onClick}>
        <Search size={16} aria-hidden className={styles.icon} />
        <span className={styles.label}>搜尋</span>
        <span className={styles.kbdHint} aria-hidden>{kbd}</span>
      </button>
    </RowTip>
  )
}

function NavRow({ register, index, def, active, collapsed }: { register: Register; index: number; def: NavDef; active: boolean; collapsed: boolean }) {
  const ref = useRef<HTMLAnchorElement>(null)
  useRegisterFluidHoverItem(register, index, ref)
  const Icon = def.icon
  return (
    <RowTip show={collapsed} label={def.label}>
    <Link
      ref={ref}
      to={def.to}
      className={styles.row}
      data-active={active || undefined}
      aria-current={active ? 'page' : undefined}
      onPointerEnter={() => preloadRoute(def.preload)}
    >
      <Icon size={16} aria-hidden className={styles.icon} />
      <span className={styles.label}>{def.label}</span>
    </Link>
    </RowTip>
  )
}

/**
 * 收合時才顯示提示（展開時文字就在列上，再浮一次是重複）。
 *
 * 提示框一律包著、展開時以 `forceOpen={false}` 關掉，而不是展開時拆掉：拆掉會讓這一列的 DOM 節點
 * 整個換新，而滑動高亮只在列第一次掛上時登錄節點（useRegisterFluidHoverItem），之後量的都是已經
 * 移除的舊節點、量不到就沿用舊值——在收合狀態下載入、再展開，選取底色就一直停在收合時的 36px 寬。
 */
function RowTip({ show, label, children }: { show: boolean; label: ReactNode; children: React.ReactElement }) {
  return <Tooltip content={label} side="right" sideOffset={10} forceOpen={show ? undefined : false}>{children}</Tooltip>
}

/**
 * 左側欄（交接包「側欄」）：Claude Desktop／Grok 式單欄，256px，可收合成 52px 圖示列。
 * 收合是同一個元件縮寬，文字淡出、圖示不位移；不做整個隱藏，也不用 Fluid 的 peek。
 *
 * 主導覽的選取是一塊滑動的底色（Fluid 的 useFluidHover 只拿來量每列位置），跟著路由滑到新的一列。
 * hover 則是每一列自己的底色，與下方「最近」對話同一個樣子：滑動的 hover 底色會落後游標，
 * 經過列間 2px 的空隙時還會淡出再重新淡入，看起來時有時無。
 *
 * 底部是個人列（帳號選單）與最右邊的使用說明鈕；收合時只剩頭像。
 */
export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { pathname } = useLocation()
  const { openSearch } = useShell()
  const mac = useIsMac()
  const navRef = useRef<HTMLElement>(null)
  const rows = useFluidHover(navRef)
  const active = activeIndexOf(pathname)
  const activeRect = useStableRect(rows, active)
  const toggleLabel = collapsed ? '展開側欄' : '收合側欄'

  return (
    <aside className={styles.sidebar} data-collapsed={collapsed} aria-label="側欄">
      <div className={styles.head}>
        <span className={styles.brand} aria-hidden={collapsed}>
          <BrandLogo size={22} alt="" />
          <span className={styles.brandName}>廷豐智能研報</span>
        </span>
        <Tooltip content={toggleLabel} side="right" sideOffset={10}>
          <button type="button" className={styles.toggle} onClick={onToggle} aria-label={toggleLabel} aria-expanded={!collapsed}>
            <PanelLeft size={16} strokeWidth={1.5} aria-hidden />
          </button>
        </Tooltip>
      </div>

      <nav ref={navRef} className={styles.nav} aria-label="主導覽">
        <FluidHoverHighlight rect={activeRect} session={0} fadeIn={false} transition={GLIDE} className={styles.hlActive} />
        <SearchRow register={rows.registerItem} kbd={`${mac ? '⌘' : 'Ctrl'} K`} mac={mac} collapsed={collapsed} onClick={openSearch} />
        {NAV.map((n, i) => (
          <NavRow key={n.to} register={rows.registerItem} index={i + 1} def={n} active={active === i + 1} collapsed={collapsed} />
        ))}
      </nav>

      <div className={styles.recents} aria-hidden={collapsed} inert={collapsed}>
        <ConversationList />
      </div>

      <div className={styles.foot}>
        <AccountMenu variant="sidebar" collapsed={collapsed} />
        <Tooltip content="使用說明" side="top" sideOffset={6}>
          <Link
            to="/help"
            className={styles.help}
            aria-label="使用說明"
            aria-hidden={collapsed}
            inert={collapsed}
            onPointerEnter={() => preloadRoute('help')}
          >
            <CircleHelp size={16} strokeWidth={1.5} aria-hidden />
          </Link>
        </Tooltip>
      </div>
    </aside>
  )
}
