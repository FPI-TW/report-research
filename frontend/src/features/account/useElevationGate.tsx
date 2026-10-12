import { useCallback, useRef, useState, type ReactNode } from 'react'
import { ApiError } from '../../lib/api'
import { ElevationDialog } from './ElevationDialog'

/** 使用者在權限提升對話框按了取消：呼叫端通常直接忽略。 */
export class ElevationCancelledError extends Error {
  constructor() {
    super('已取消重新驗證')
    this.name = 'ElevationCancelledError'
  }
}

export type ElevateFn = (body: { password: string; code?: string | null }) => Promise<unknown>

const isElevationRequired = (err: unknown) => err instanceof ApiError && err.code === 'elevation_required'

/**
 * 「需要重新驗證」的閘門。
 *
 * 敏感操作（調整權限、刪除帳號、重設 TOTP、關閉自己的 TOTP）後端要求近 10 分鐘內在這個 session
 * 重新驗證過，否則回 403 `elevation_required`。`guard(action)` 先直接執行；收到那個代碼時彈出
 * 對話框，驗證成功後**自動重試同一個動作**，呼叫端拿到的就是重試的結果。使用者取消時 reject
 * `ElevationCancelledError`。
 *
 * 帳號開了 TOTP 時要一起輸入驗證碼：`needTotp` 由 `/api/me` 的 `totp_enabled` 決定；若沒帶到，
 * 後端回 `totp_required` 時也會把驗證碼欄位打開。
 */
export function useElevationGate(elevate: ElevateFn, needTotp: boolean) {
  const pendingRef = useRef<{ retry: () => void; cancel: () => void } | null>(null)
  const [open, setOpen] = useState(false)

  const guard = useCallback(<T,>(action: () => Promise<T>): Promise<T> =>
    action().catch((err: unknown) => {
      if (!isElevationRequired(err)) throw err
      return new Promise<T>((resolve, reject) => {
        pendingRef.current = {
          retry: () => { action().then(resolve, reject) },
          cancel: () => reject(new ElevationCancelledError()),
        }
        setOpen(true)
      })
    }), [])

  const finish = (ok: boolean) => {
    const pending = pendingRef.current
    pendingRef.current = null
    setOpen(false)
    if (ok) pending?.retry()
    else pending?.cancel()
  }

  const dialog: ReactNode = (
    <ElevationDialog open={open} needTotp={needTotp} elevate={elevate}
      onDone={() => finish(true)} onCancel={() => finish(false)} />
  )
  // open：外層若也是 Modal，Escape 會同時送到兩個對話框；外層據此在驗證框開著時忽略關閉。
  return { guard, dialog, open }
}
