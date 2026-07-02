import { Menu, UnstyledButton } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { getStats } from '../features/search/api'
import avatarUrl from '../assets/avatar.jpg'

/** 帳號選單：頭像鈕 → 帳號名 + 登出。
 *  登出維持原生 form POST /logout（伺服器 303 → /login），無需 JS。 */
export function AccountMenu({ position = 'right-end' }: { position?: 'right-end' | 'top-end' }) {
  // 與 SearchPage/AppRail 共用 queryKey ['stats'] → 快取共享，僅取 username。
  const { data } = useQuery({ queryKey: ['stats'], queryFn: getStats, staleTime: 5 * 60_000 })
  const name = (data?.username || '').trim() || '使用者'
  return (
    <Menu position={position} withArrow transitionProps={{ duration: 0 }}>
      <Menu.Target>
        <UnstyledButton aria-label="帳號選單" style={{ display: 'block', lineHeight: 0 }}>
          <img
            data-testid="account-avatar"
            src={avatarUrl}
            alt=""
            width={30}
            height={30}
            style={{ borderRadius: '50%', display: 'block' }}
          />
        </UnstyledButton>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label title="目前登入帳號">{name}</Menu.Label>
        <form method="post" action="/logout" style={{ margin: 0 }}>
          <Menu.Item component="button" type="submit">
            登出
          </Menu.Item>
        </form>
      </Menu.Dropdown>
    </Menu>
  )
}
