import { useSyncExternalStore } from 'react'

function readMac(): boolean {
  if (typeof navigator === 'undefined') return false
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } }
  return /mac|iphone|ipad/i.test(nav.userAgentData?.platform ?? nav.platform ?? '')
}

const subscribeNever = () => () => {}

/** 快捷鍵提示用：Mac 顯示 ⌘，其他平台顯示 Ctrl。平台在工作階段中不會變，所以不訂閱任何事件。 */
export function useIsMac(): boolean {
  return useSyncExternalStore(subscribeNever, readMac, () => false)
}
