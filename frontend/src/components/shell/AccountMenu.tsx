import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { ChevronsUpDown, CircleHelp, LogOut, Settings } from 'lucide-react'
import { DropdownContent, DropdownLabel, DropdownMenu, DropdownSeparator, DropdownTrigger } from '@/components/ui/dropdown'
import { MenuItem } from '@/components/ui/menu-item'
import { Tooltip } from '@/components/ui/tooltip'
import { preloadRoute } from '../../lib/routePreload'
import { useMe } from '../../lib/useMe'
import { useStats } from '../../lib/useStats'
import { STATUS_LABELS, useSystemStatus } from '../../lib/useSystemStatus'
import { StatusDot } from './SystemStatus'
import { useShell } from './shellContext'
import styles from './AccountMenu.module.css'

const ROLE_LABEL: Record<string, string> = { admin: '管理員', user: '使用者' }

/**
 * 帳號選單（交接包「帳號選單」）：側欄底部的個人列往上開，只有設定、使用說明、登出。
 *
 * - **不放「管理後台」入口**（含管理員帳號）：避免資安疑慮，管理員直接輸入 `/app/admin`。
 *   授權只在後端 `web/authz.py`，這裡本來就只是顯示層。
 * - 系統狀態、語言、帳號安全都移到設定彈窗（「關於」「偏好」「安全」分頁）。正常時不顯示任何狀態；
 *   只有異常時在頭像角落亮紅點、title 寫一句，細節在設定的「關於」。
 */
export function AccountMenu({ variant, collapsed = false }: { variant: 'sidebar' | 'mobile'; collapsed?: boolean }) {
  const { data: stats } = useStats()
  const me = useMe()
  const name = me.data?.username ?? stats?.username ?? '使用者'
  const role = ROLE_LABEL[me.data?.role ?? 'user'] ?? '使用者'
  const initial = name.slice(0, 1).toUpperCase()
  const [open, setOpen] = useState(false)
  const { openSettings } = useShell()
  const navigate = useNavigate()
  const logoutRef = useRef<HTMLFormElement>(null)
  const degraded = useSystemStatus().status === 'degraded'
  const title = degraded ? `${name}（系統狀態：${STATUS_LABELS.degraded}）` : undefined
  const avatar = (
    <span className={styles.avatar} aria-hidden>
      {initial}
      {degraded && <StatusDot level="degraded" className={styles.alertDot} />}
    </span>
  )

  const trigger = variant === 'sidebar' ? (
    <button type="button" className={styles.row} data-open={open || undefined} title={title} aria-label={collapsed ? `帳號選單：${name}` : undefined}>
      {avatar}
      <span className={styles.text}>
        <span className={styles.name}>{name}</span>
        <span className={styles.role}>{role}</span>
      </span>
      <ChevronsUpDown size={14} strokeWidth={1.5} aria-hidden className={styles.chev} />
    </button>
  ) : (
    <button type="button" className={styles.avatarBtn} title={title} aria-label={`帳號選單：${name}`}>
      {avatar}
    </button>
  )

  return (
    <>
      <DropdownMenu open={open} onOpenChange={setOpen}>
        {collapsed ? (
          <Tooltip content={name} side="right" sideOffset={10}>
            <DropdownTrigger render={trigger} />
          </Tooltip>
        ) : (
          <DropdownTrigger render={trigger} />
        )}
        <DropdownContent side="top" align="start" sideOffset={6} className={styles.menu}>
          <DropdownLabel>{me.data?.username ?? name}</DropdownLabel>
          <MenuItem index={0} icon={Settings} label="設定" onSelect={() => openSettings('account')} />
          <MenuItem
            index={1}
            icon={CircleHelp}
            label="使用說明"
            onSelect={() => navigate('/help')}
            onPointerEnter={() => preloadRoute('help')}
          />
          <DropdownSeparator />
          <MenuItem index={2} icon={LogOut} label="登出" onSelect={() => logoutRef.current?.requestSubmit()} />
        </DropdownContent>
      </DropdownMenu>
      {/* 登出要 POST（CSRF 守門在後端）；選單項目觸發這張隱藏表單。 */}
      <form ref={logoutRef} method="post" action="/logout" hidden aria-hidden />
    </>
  )
}
