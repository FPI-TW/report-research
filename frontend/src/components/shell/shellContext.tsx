import { createContext, useContext } from 'react'

/** 外殼層級的彈窗開關：側欄、帳號選單、快捷鍵與各頁都可能要開搜尋或設定。 */
export interface ShellActions {
  openSearch: () => void
  openSettings: (tab?: SettingsTab) => void
}

export type SettingsTab = 'account' | 'security' | 'prefs' | 'about'

const noop = () => {}

export const ShellContext = createContext<ShellActions>({ openSearch: noop, openSettings: noop })

export function useShell(): ShellActions {
  return useContext(ShellContext)
}
