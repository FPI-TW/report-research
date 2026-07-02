import IconActivity from '@tabler/icons-react/dist/esm/icons/IconActivity.mjs'
import IconMessages from '@tabler/icons-react/dist/esm/icons/IconMessages.mjs'
import IconSearch from '@tabler/icons-react/dist/esm/icons/IconSearch.mjs'

/** 全站主導覽連結（桌機左軌與手機底欄共用）。文案沿用現有「搜尋/問答/監控」平價。 */
export const NAV_LINKS = [
  { to: '/search', label: '搜尋', Icon: IconSearch },
  { to: '/ask', label: '問答', Icon: IconMessages },
  { to: '/monitor', label: '監控', Icon: IconActivity },
] as const

/** 手機斷點：<=48em 用底部分頁列，否則桌機左軌。 */
export const MOBILE_NAV_QUERY = '(max-width: 48em)'
