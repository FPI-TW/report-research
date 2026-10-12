import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router'
import { motion, useReducedMotion } from 'motion/react'
import { Moon, Sun } from 'lucide-react'
import { BrandLogo } from '../BrandLogo'
import { Icon } from '../primitives/Icon'
import { Lamp } from '../primitives/Lamp'
import { fmtDateTime } from '../../features/admin/auditLabels'
import { worstLamp, type AdminSignals } from '../../features/admin/adminSignals'
import { useAdminSignals } from '../../features/admin/useAdminSignals'
import { useAdminTheme, type AdminTheme } from '../../lib/adminTheme'
import { TF_DUR, TF_EASE_OUT } from '../../lib/motionTokens'
import { useMe } from '../../lib/useMe'
import { useMfaEnrollmentSignal } from '../../lib/useMfaEnrollment'
import { AdminMfaRequired } from './AdminMfaRequired'
import { AdminPalette } from './AdminPalette'
import {
  flattenPages, locate, pageMatches, visibleDomains,
  type AdminDomain, type AdminGroup, type AdminPage, type AdminPageEntry,
} from './adminNav'
import { RequireAdmin } from './RequireAdmin'
import styles from './AdminShell.module.css'

const LAMP_TEXT = { bad: '有異常', warn: '需要注意' } as const
const IS_MAC = typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.userAgent)
const PALETTE_HINT = IS_MAC ? '⌘K' : 'Ctrl K'

/**
 * 管理後台（/app/admin/*）的外殼，與研報平台的 `AppShell` 完全分開：圖示軌（7 個領域）＋
 * 次級面板（該領域的頁面）＋頂列（麵包屑、深淺色切換、⌘K 頁面搜尋、帳號、回研報平台）。
 * 導覽結構在 `adminNav.ts`，燈號與數字在 `features/admin/adminSignals.ts`，主題在 `lib/adminTheme.ts`。
 *
 * 守門放在外殼這一層：還不確定是管理員之前不畫領域與頁面導覽、不送任何管理請求，內容區交給
 * `RequireAdmin`。**這仍只是顯示層**——授權一律由後端 `web/authz.py` 判，繞過前端也只會拿到 403。
 * 同一套 SPA、同一個 build；只是另一組 layout route（見 `App.tsx`）。
 *
 * 管理員 TOTP 強制（後端 `ADMIN_MFA_REQUIRED`）也在這一處處理：`/api/me` 說 `mfa_enrollment_required`
 * 時，導覽與內容換成 TOTP 設定頁（`AdminMfaRequired`）；任何管理 API 回 403 `mfa_enrollment_required`
 * 都會讓 `/api/me` 立刻重取（`useMfaEnrollmentSignal`），不必等快取過期。
 */
export function AdminShell() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const reduced = useReducedMotion()
  const me = useMe()
  useMfaEnrollmentSignal()
  const { theme, setTheme } = useAdminTheme()
  const [paletteOpen, setPaletteOpen] = useState(false)

  const mfaRequired = Boolean(me.data?.mfa_enrollment_required)
  const ready = me.data?.role === 'admin' && !mfaRequired
  const scopeList = me.data?.scopes
  const scopes = useMemo(() => scopeList ?? [], [scopeList])
  const domains = useMemo(() => (ready ? visibleDomains(scopes) : []), [ready, scopes])
  const pages = useMemo(() => flattenPages(domains), [domains])
  const { signals, env } = useAdminSignals({ enabled: ready, scopes })
  const elevated = useElevated(me.data?.elevated_until)

  const here = locate(domains, pathname)
  const domain = here?.domain ?? domains[0]

  useEffect(() => {
    if (!ready) return
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen(open => !open)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [ready])

  const closePalette = useCallback(() => setPaletteOpen(false), [])
  const goFromPalette = useCallback((to: string) => { setPaletteOpen(false); void navigate(to) }, [navigate])

  return (
    <div className={styles.shell}>
      <aside className={styles.rail}>
        <BrandLogo size={30} className={styles.logo} />
        {domains.length > 0 && (
          <nav className={styles.domains} aria-label="管理領域">
            {domains.map(d => <RailItem key={d.key} domain={d} on={d.key === domain?.key} signals={signals} />)}
          </nav>
        )}
        <div className={styles.railSpacer} />
        <Link to="/search" className={styles.railAction} title="回到研報平台" aria-label="回到研報平台">
          <Icon name="arrowLeft" size={18} />
        </Link>
        <form method="post" action="/logout" className={styles.railForm}>
          <button type="submit" className={styles.railAction} title="登出" aria-label="登出">
            <Icon name="logout" size={18} />
          </button>
        </form>
      </aside>

      {domain && (
        <aside className={styles.panel}>
          <div className={styles.panelHead}>
            <div className={styles.panelKicker}>廷豐智能研報・管理後台</div>
            <div className={styles.panelTitle}>{domain.label}</div>
          </div>
          <nav className={styles.panelNav} aria-label="管理導覽">
            {domain.groups.map((g, i) => (
              <PanelGroup key={g.label ?? i} group={g} pathname={pathname} signals={signals} />
            ))}
            {domain.key === 'home' && <AttentionGroup pages={pages} signals={signals} />}
          </nav>
          {env && (
            <div className={styles.env} aria-label="環境">
              <div className={styles.envName}><Lamp tone="ok" label="" />{env.environment}</div>
              <div>{env.host}</div>
              <div>檢查於 {fmtDateTime(env.checked_at)}</div>
            </div>
          )}
        </aside>
      )}

      <div className={styles.column}>
        <header className={styles.topbar}>
          <nav className={styles.crumb} aria-label="位置">
            {here ? (
              <>
                <span>{here.domain.label}</span>
                {here.group.label && <><span className={styles.sep} aria-hidden="true">/</span><span>{here.group.label}</span></>}
                <span className={styles.sep} aria-hidden="true">/</span>
                <span className={styles.crumbPage} aria-current="page">{here.page.label}</span>
              </>
            ) : <span>管理後台</span>}
          </nav>
          <ThemeSwitch theme={theme} onChange={setTheme} />
          {ready && (
            <button type="button" className={styles.search} onClick={() => setPaletteOpen(true)} aria-keyshortcuts="Meta+K Control+K">
              <Icon name="search" size={14} />
              <span className={styles.searchLabel}>搜尋頁面</span>
              <kbd className={styles.kbd}>{PALETTE_HINT}</kbd>
            </button>
          )}
          <span className={styles.divider} aria-hidden="true" />
          {me.data?.username && (
            <span className={styles.who}>
              {me.data.username}
              {me.data.is_super && <span className={styles.super}>super</span>}
            </span>
          )}
          {elevated && (
            <span className={styles.elevated} title="10 分鐘內不用再驗證"><Lamp tone="ok" label="" />已驗證</span>
          )}
          <Link to="/search" className={styles.back}><Icon name="arrowLeft" size={14} />研報平台</Link>
        </header>
        <RequireAdmin>
          {mfaRequired ? (
            <main className={styles.main}><AdminMfaRequired /></main>
          ) : (
            <main className={styles.main}>
              <motion.div
                key={pathname}
                className={styles.routeReveal}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: reduced ? 0 : TF_DUR.d2, ease: TF_EASE_OUT }}
              >
                <Outlet />
              </motion.div>
            </main>
          )}
        </RequireAdmin>
      </div>

      <AdminPalette open={paletteOpen && ready} pages={pages} onClose={closePalette} onGo={goFromPalette} />
    </div>
  )
}

/** 圖示軌的一格：點了到該領域的第一頁。底下的頁面有 bad 亮紅點，只有 warn 亮橘點；總覽不亮。 */
function RailItem({ domain, on, signals }: { domain: AdminDomain; on: boolean; signals: AdminSignals }) {
  const keys = domain.groups.flatMap(g => g.pages.map(p => p.key))
  const dot = domain.key === 'home' ? null : worstLamp(signals, keys)
  return (
    <Link
      to={domain.groups[0].pages[0].to}
      className={`${styles.railItem} ${on ? styles.railOn : ''}`}
      title={domain.label}
      aria-label={dot ? `${domain.label}（${LAMP_TEXT[dot]}）` : domain.label}
      aria-current={on ? 'true' : undefined}
    >
      <Icon name={domain.icon} size={20} />
      <span className={styles.railLabel}>{domain.label}</span>
      {dot && <span className={styles.railDot} data-tone={dot} aria-hidden="true" />}
    </Link>
  )
}

function PanelGroup({ group, pathname, signals }: { group: AdminGroup; pathname: string; signals: AdminSignals }) {
  return (
    <>
      {group.label && <div className={styles.groupLabel}>{group.label}</div>}
      {group.pages.map(p => (
        <PanelItem key={p.key} page={p} on={pageMatches(p, pathname)} signals={signals} />
      ))}
    </>
  )
}

/** 總覽領域多一組「異常」：列出目前亮燈的頁面當捷徑（bad 在前）。沒有亮燈就不畫。 */
function AttentionGroup({ pages, signals }: { pages: readonly AdminPageEntry[]; signals: AdminSignals }) {
  const lit = pages.filter(e => signals[e.page.key]?.lamp)
  if (lit.length === 0) return null
  const ordered = [...lit].sort((a, b) => rank(signals[a.page.key]?.lamp) - rank(signals[b.page.key]?.lamp))
  return (
    <>
      <div className={styles.groupLabel}>異常</div>
      {ordered.map(e => <PanelItem key={e.page.key} page={e.page} on={false} signals={signals} />)}
    </>
  )
}

const rank = (lamp: 'bad' | 'warn' | undefined) => (lamp === 'bad' ? 0 : 1)

function PanelItem({ page, on, signals }: { page: AdminPage; on: boolean; signals: AdminSignals }) {
  const s = signals[page.key]
  return (
    <Link
      to={page.to}
      className={`${styles.item} ${on ? styles.itemOn : ''}`}
      aria-current={on ? 'page' : undefined}
    >
      <span className={styles.itemLabel}>{page.label}</span>
      {s?.lamp && <Lamp tone={s.lamp} label={LAMP_TEXT[s.lamp]} />}
      {s?.count ? <span className={styles.count}>{s.count}</span> : null}
    </Link>
  )
}

function ThemeSwitch({ theme, onChange }: { theme: AdminTheme; onChange: (t: AdminTheme) => void }) {
  return (
    <div className={styles.themeSeg} role="group" aria-label="主題">
      <button type="button" className={styles.themeBtn} title="深色" aria-label="深色"
        aria-pressed={theme === 'dark'} onClick={() => onChange('dark')}>
        <Moon size={15} strokeWidth={1.8} aria-hidden="true" />
      </button>
      <button type="button" className={styles.themeBtn} title="亮色" aria-label="亮色"
        aria-pressed={theme === 'light'} onClick={() => onChange('light')}>
        <Sun size={15} strokeWidth={1.8} aria-hidden="true" />
      </button>
    </div>
  )
}

/**
 * 重新驗證的視窗還開著嗎（`/api/me` 的 `elevated_until`，後端 elevate 流程寫的）。
 * 到期那一刻自己翻回 false，不必等 `/api/me` 重取。
 */
function useElevated(until: string | null | undefined): boolean {
  const expiresAt = until ? Date.parse(until) : NaN
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (Number.isNaN(expiresAt)) return
    const ms = expiresAt - Date.now()
    if (ms <= 0) return
    const id = window.setTimeout(() => setNow(Date.now()), ms + 50)
    return () => window.clearTimeout(id)
  }, [expiresAt])
  return !Number.isNaN(expiresAt) && expiresAt > now
}
