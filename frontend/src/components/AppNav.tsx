import { ActionIcon, Group, Text, Tooltip } from '@mantine/core'
import { useMediaQuery } from '@mantine/hooks'
import { Link, NavLink } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { getStats } from '../features/search/api'
import avatarUrl from '../assets/avatar.jpg'

const LINKS: ReadonlyArray<{ to: string; label: string }> = [
  { to: '/search', label: '搜尋' },
  { to: '/ask', label: '問答' },
  { to: '/monitor', label: '監控' },
]

const noUnderline = { textDecoration: 'none' } as const

/** 帳號區（右側）：頭像 + 共用帳號名 + 登出，對齊 vanilla 側欄帳號區。 */
function AccountArea() {
  // 與 SearchPage 共用 queryKey ['stats'] → 快取共享，僅取其中的 username。
  const { data } = useQuery({ queryKey: ['stats'], queryFn: getStats, staleTime: 5 * 60_000 })
  const isMobile = useMediaQuery('(max-width: 40em)', false, { getInitialValueInEffect: false })
  const isNarrow = useMediaQuery('(max-width: 23em)', false, { getInitialValueInEffect: false })
  const name = (data?.username || '').trim() || '使用者'
  return (
    <Group gap="xs" wrap="nowrap">
      {!isNarrow && (
        <img
          data-testid="account-avatar"
          src={avatarUrl}
          alt=""
          width={28}
          height={28}
          style={{ borderRadius: '50%', display: 'block' }}
        />
      )}
      {!isMobile && (
        <Text size="sm" c="dimmed" title="目前登入帳號" style={{ whiteSpace: 'nowrap' }}>
          {name}
        </Text>
      )}
      {/* 原生 form POST /logout（伺服器回 303 → /login），對齊 vanilla、無需 JS。 */}
      <form method="post" action="/logout" style={{ margin: 0, display: 'inline-flex' }}>
        <Tooltip label="登出">
          <ActionIcon type="submit" variant="subtle" color="gray" aria-label="登出">
            <svg
              width={16}
              height={16}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2.2}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <path d="M16 17l5-5-5-5" />
              <path d="M21 12H9" />
            </svg>
          </ActionIcon>
        </Tooltip>
      </form>
    </Group>
  )
}

export function AppNav() {
  const isMobile = useMediaQuery('(max-width: 40em)', false, { getInitialValueInEffect: false })
  const isNarrow = useMediaQuery('(max-width: 23em)', false, { getInitialValueInEffect: false })
  return (
    <Group h="100%" px={isMobile ? 'xs' : 'md'} justify="space-between" wrap="nowrap">
      <Link to="/search" style={noUnderline}>
        <Text fw={700} c="gold.7" size={isNarrow ? 'md' : 'lg'} style={{ whiteSpace: 'nowrap' }}>
          廷豐智能研報
        </Text>
      </Link>
      <Group gap={isMobile ? 'xs' : 'sm'} wrap="nowrap">
        <Group component="nav" aria-label="主導覽" gap={isMobile ? 'xs' : 'sm'} wrap="nowrap">
          {LINKS.map(({ to, label }) => (
            <NavLink key={to} to={to} style={noUnderline}>
              {({ isActive }) => (
                <Text
                  fw={isActive ? 700 : 500}
                  c={isActive ? 'gold.7' : 'dimmed'}
                  size={isNarrow ? 'sm' : 'md'}
                  style={{ whiteSpace: 'nowrap' }}
                >
                  {label}
                </Text>
              )}
            </NavLink>
          ))}
        </Group>
        <AccountArea />
      </Group>
    </Group>
  )
}
