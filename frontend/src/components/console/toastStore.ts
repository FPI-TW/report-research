export interface ToastState { id: number; message: string; tone: 'ok' | 'bad' }

/** 操作結果停留的時間（設計稿：2.6 秒）。 */
export const TOAST_MS = 2600
/** 失敗訊息多半是後端的 detail，比較長，也需要被看到：停久一點。 */
export const TOAST_BAD_MS = 6000

let current: ToastState | null = null
let timer: ReturnType<typeof setTimeout> | undefined
let seq = 0
const listeners = new Set<() => void>()
const emit = () => listeners.forEach(fn => fn())

/**
 * 顯示操作結果：底部置中、2.6 秒消失（失敗的停 6 秒），新的蓋掉舊的。文案短句、直接講結果
 * （「已停用「jason.wu」」「批次停用：成功 3 筆、略過 0 筆」）。失敗用 `tone: 'bad'`。
 *
 * 刻意做成模組層的函式而不是 context：頁面不必包 provider 就能呼叫；畫面由外殼的 `<Toaster />`
 * 負責。測試要斷言訊息時，在測試的 render 裡一起放 `<Toaster />`。
 */
export function toast(message: string, opts: { tone?: 'ok' | 'bad' } = {}): void {
  clearTimeout(timer)
  current = { id: ++seq, message, tone: opts.tone ?? 'ok' }
  emit()
  timer = setTimeout(() => { current = null; emit() }, current.tone === 'bad' ? TOAST_BAD_MS : TOAST_MS)
}

/** 測試用：清掉還沒消失的 toast。 */
export function dismissToast(): void {
  clearTimeout(timer)
  current = null
  emit()
}

export const subscribeToast = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn) } }
export const toastSnapshot = () => current
