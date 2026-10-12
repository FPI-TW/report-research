import { useCallback, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ElevationCancelledError } from '../../features/account/useElevationGate'
import type { Me } from '../../lib/adminSchemas'
import { ApiError } from '../../lib/api'
import { useMe } from '../../lib/useMe'
import { ElevateDialog } from './ElevateDialog'

/** 使用者在「重新驗證身分」按了取消：不是錯誤，呼叫端通常直接忽略。 */
export function isElevationCancelled(err: unknown): boolean {
  return err instanceof ElevationCancelledError
}

const isElevationRequired = (err: unknown) => err instanceof ApiError && err.code === 'elevation_required'

interface Pending { what: string; run: () => void; cancel: () => void }

/**
 * 「重新驗證身分」的閘門，接後端既有的 elevate 流程（`POST /api/me/elevate`，視窗 10 分鐘）。
 *
 * 敏感操作（刪除帳號、調整權限、重設 2FA、批次帳號操作、撤銷 session、調整配額、建立／輪替 API 金鑰、
 * 變更旗標、重新啟動／立即執行服務）用 `elevated('調整權限', () => mutation.mutateAsync(...))` 包起來：
 *
 * - `/api/me` 的 `elevated_until` 還沒到期 → 直接執行，不再詢問。
 * - 否則先跳對話框（密碼＋開了兩步驟驗證時的 6 位數驗證碼），驗證成功才執行，並把新的到期時間寫回
 *   `/api/me` 的快取——頂列的「已驗證」跟著出現，10 分鐘內其他操作也不再詢問。
 * - 前端以為還在視窗內、後端卻回 403 `elevation_required`（時鐘差、別處登出）時，同樣跳對話框後重試一次。
 * - 使用者取消 → reject `ElevationCancelledError`（用 `isElevationCancelled` 判斷後忽略）。
 *
 * 這只是流程：擋人的是後端 `require_elevated`。回傳的 `dialog` 要放進頁面的 JSX。
 */
export function useElevate(): { elevated: <T>(what: string, action: () => Promise<T>) => Promise<T>; dialog: ReactNode } {
  const me = useMe()
  const client = useQueryClient()
  const [pending, setPending] = useState<Pending | null>(null)

  const prompt = useCallback(<T,>(what: string, action: () => Promise<T>) => new Promise<T>((resolve, reject) => {
    setPending({
      what,
      run: () => { action().then(resolve, reject) },
      cancel: () => reject(new ElevationCancelledError()),
    })
  }), [])

  const elevated = useCallback(<T,>(what: string, action: () => Promise<T>): Promise<T> => {
    // 到期時間在呼叫當下從 `/api/me` 的快取讀：剛驗證完寫回去的值馬上就看得到。
    const stored = client.getQueryData<Me>(['me'])?.elevated_until
    const until = stored ? Date.parse(stored) : NaN
    if (Number.isNaN(until) || until <= Date.now()) return prompt(what, action)
    return action().catch((err: unknown) => {
      if (!isElevationRequired(err)) throw err
      return prompt(what, action)
    })
  }, [client, prompt])

  const finish = (until: string | null) => {
    const p = pending
    setPending(null)
    if (!p) return
    if (until === null) { p.cancel(); return }
    client.setQueryData<Me>(['me'], old => (old ? { ...old, elevated_until: until } : old))
    p.run()
  }

  const dialog = pending && (
    <ElevateDialog what={pending.what} needTotp={Boolean(me.data?.totp_enabled)}
      onDone={until => finish(until)} onCancel={() => finish(null)} />
  )
  return { elevated, dialog }
}
