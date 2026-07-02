import { Menu, UnstyledButton } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { getStats } from '../features/search/api'
import avatarUrl from '../assets/avatar.jpg'

/** 帳號選單：頭像鈕 → 帳號名 + 登出。
 *  登出維持原生 form POST /logout（伺服器 303 → /login），無需 JS。
 *  `label` 可選：手機底部分頁列第 4 格傳入文字，讓整格（含文字）可點，
 *  觸控目標達 WCAG 建議尺寸；桌機 AppRail 不傳，維持原本純頭像鈕。 */
export function AccountMenu({
  position = 'right-end',
  label,
}: {
  position?: 'right-end' | 'top-end'
  label?: string
}) {
  // 與 SearchPage/AppRail 共用 queryKey ['stats'] → 快取共享，僅取 username。
  const { data } = useQuery({ queryKey: ['stats'], queryFn: getStats, staleTime: 5 * 60_000 })
  const name = (data?.username || '').trim() || '使用者'
  return (
    <Menu position={position} withArrow transitionProps={{ duration: 0 }} closeOnItemClick={false}>
      <Menu.Target>
        <UnstyledButton
          aria-label="帳號選單"
          style={
            label
              ? {
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 3,
                  width: '100%',
                  height: '100%',
                  fontSize: 10,
                  lineHeight: 1,
                  color: 'var(--tf-text-3)',
                }
              : { display: 'block', lineHeight: 0 }
          }
        >
          <img
            data-testid="account-avatar"
            src={avatarUrl}
            alt=""
            width={30}
            height={30}
            style={{ borderRadius: '50%', display: 'block' }}
          />
          {label && <span>{label}</span>}
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
