import { useSyncExternalStore } from 'react'

export interface ToastItem {
  id: number
  message: string
  /** 成功類訊息前面加綠勾。 */
  ok: boolean
}

let current: ToastItem | null = null
let seq = 0
let timer: ReturnType<typeof setTimeout> | undefined
const listeners = new Set<() => void>()

function emit() { listeners.forEach(l => l()) }

/** 底部置中的提示訊息（交接包「互動」）：一次只顯示一則，2 秒後淡出。 */
export function showToast(message: string, opts: { ok?: boolean } = {}): void {
  clearTimeout(timer)
  current = { id: ++seq, message, ok: opts.ok ?? false }
  emit()
  timer = setTimeout(() => { current = null; emit() }, 2000)
}

/** 測試用：清掉目前的提示與計時器。 */
export function __resetToast(): void {
  clearTimeout(timer)
  current = null
  emit()
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => { listeners.delete(cb) }
}

export function useToast(): ToastItem | null {
  return useSyncExternalStore(subscribe, () => current, () => null)
}
