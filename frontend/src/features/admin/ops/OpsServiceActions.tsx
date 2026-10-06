import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError } from '../../../lib/api'
import { adminApi, type OpsServiceDetail } from '../../../lib/generated/adminApi'
import { useHasScope, useMe } from '../../../lib/useMe'
import { ElevationCancelledError, useElevationGate } from '../../account/useElevationGate'
import { OPS_KEY } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

type WriteAction = 'restart' | 'run'

const LABELS: Record<WriteAction, string> = { restart: '重新啟動', run: '立即執行' }

/** 後端錯誤代碼 → 給人看的一句話；其餘沿用後端 detail（requestJSON 已放進 message）。 */
function messageOf(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'already_running') return '同一組工作正在執行中，請等它結束後再試（不排隊）。'
    if (err.code === 'action_not_allowed') return '這個服務不允許這項操作。'
    if (err.code === 'ops_agent_unavailable') return '維運代理目前無法使用，操作沒有送出。'
    if (err.code === 'missing_scope') return '需要「ops.operate」權限。'
  }
  return err instanceof Error ? err.message : String(err)
}

/**
 * 服務的寫入類操作（P7）：web 的重新啟動、白名單批次的立即執行。
 *
 * 只是顯示層：沒有 `ops.operate` 或 catalog 沒開放的服務不顯示按鈕；真正擋人的是後端
 * （require_scope＋require_elevated、代理端白名單與 execution group 互斥）。需要重新驗證時由
 * useElevationGate 彈出驗證框、驗證後自動重試。重新啟動先在頁面上確認一次——web 重啟期間這個
 * 頁面也會短暫連不上。
 */
export function OpsServiceActions({ service }: { service: OpsServiceDetail }) {
  const canOperate = useHasScope('ops.operate')
  const me = useMe()
  const client = useQueryClient()
  const { guard, dialog } = useElevationGate(adminApi.elevate, Boolean(me.data?.totp_enabled))
  const [confirming, setConfirming] = useState<WriteAction | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const actions = service.actions.filter((a): a is WriteAction => a === 'restart' || a === 'run')
  if (!canOperate || actions.length === 0) return null

  const execute = async (action: WriteAction) => {
    setConfirming(null)
    setBusy(true)
    setMessage(null)
    try {
      await guard(() => (action === 'restart'
        ? adminApi.restartOpsService(service.name)
        : adminApi.runOpsService(service.name)))
      setMessage({
        ok: true,
        text: action === 'restart'
          ? '已送出重新啟動，約 1–2 秒後執行；重啟期間頁面會短暫連不上，稍後狀態會自動更新。'
          : '已送出立即執行，狀態稍後會自動更新。',
      })
      // 代理先回 202 再執行；等一下再重抓，才看得到新的 ActiveState／InvocationID。
      window.setTimeout(() => { void client.invalidateQueries({ queryKey: [...OPS_KEY] }) }, 3000)
    } catch (err) {
      if (err instanceof ElevationCancelledError) return
      setMessage({ ok: false, text: messageOf(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={styles.opsActions} aria-label="服務操作">
      {confirming ? (
        <div className={styles.opsConfirm} role="group" aria-label={`確認${LABELS[confirming]}`}>
          <span>
            {confirming === 'restart'
              ? `確定要重新啟動「${service.name}」？重啟期間服務會短暫中斷。`
              : `確定要立即執行「${service.name}」一次？`}
          </span>
          <button type="button" className={adminStyles.action} disabled={busy} onClick={() => void execute(confirming)}>
            確定{LABELS[confirming]}
          </button>
          <button type="button" className={adminStyles.action} disabled={busy} onClick={() => setConfirming(null)}>
            取消
          </button>
        </div>
      ) : (
        actions.map((a) => (
          <button key={a} type="button" className={adminStyles.action} disabled={busy} onClick={() => setConfirming(a)}>
            {LABELS[a]}
          </button>
        ))
      )}
      {message && (
        <p className={message.ok ? adminStyles.hint : adminStyles.error} role={message.ok ? 'status' : 'alert'}>
          {message.text}
        </p>
      )}
      {dialog}
    </div>
  )
}
