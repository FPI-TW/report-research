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

// 切換介面語系會重掛整棵畫面樹（src/i18n 的 LocaleBoundary），外殼的彈窗狀態跟著歸零。在設定彈窗裡換語言時
// 先記下要回到的分頁，重掛後的外殼以它為初始狀態：使用者看到的是同一個彈窗換了語言，而不是彈窗消失。
let reopen: SettingsTab | null = null

export function reopenSettingsAfterRemount(tab: SettingsTab): void {
  reopen = tab
}

/** 取出並清掉待重開的分頁（外殼的初始狀態用）。 */
export function takeReopenSettings(): SettingsTab | null {
  const tab = reopen
  reopen = null
  return tab
}
