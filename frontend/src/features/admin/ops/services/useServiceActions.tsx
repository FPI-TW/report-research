import { useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { ConfirmDialog, isElevationCancelled, toast, useElevate } from '../../../../components/console'
import { ApiError } from '../../../../lib/api'
import { adminApi, type OpsServiceStatus } from '../../../../lib/generated/adminApi'
import { useHasScope } from '../../../../lib/useMe'
import { OPS_KEY } from '../useOps'

export type WriteAction = 'restart' | 'run'

export const WRITE_ACTION_LABELS: Record<WriteAction, string> = { restart: '重新啟動', run: '立即執行' }

/** catalog 對這個服務開放的寫入類操作（白名單在主機上的 Service Catalog；這裡只是照著顯示）。 */
export function writeActionsOf(service: Pick<OpsServiceStatus, 'actions'>): WriteAction[] {
  return service.actions.filter((a): a is WriteAction => a === 'restart' || a === 'run')
}

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

interface Pending { name: string; action: WriteAction }

/**
 * 服務的寫入類操作（P7）：web 的重新啟動、白名單批次的立即執行。服務表的「更多」與詳情抽屜共用這一套流程。
 *
 * 只是顯示層：沒有 `ops.operate`（`canOperate`）或 catalog 沒開放的服務，呼叫端不顯示入口；真正擋人的是後端
 * （require_scope＋require_elevated、代理端白名單與 execution group 互斥）。流程：先確認一次（web 重啟期間
 * 這個頁面也會短暫連不上）→ 經 `useElevate` 重新驗證 → 送出。結果用 toast；`dialogs` 要放進 JSX。
 */
export function useServiceActions(): {
  canOperate: boolean
  busy: boolean
  request: (name: string, action: WriteAction) => void
  dialogs: ReactNode
} {
  const canOperate = useHasScope('ops.operate')
  const client = useQueryClient()
  const { elevated, dialog } = useElevate()
  const [pending, setPending] = useState<Pending | null>(null)
  const [busy, setBusy] = useState(false)

  const execute = async ({ name, action }: Pending) => {
    setPending(null)
    setBusy(true)
    try {
      const res = await elevated(`${WRITE_ACTION_LABELS[action]} ${name}`, () => (action === 'restart'
        ? adminApi.restartOpsService(name)
        : adminApi.runOpsService(name)))
      const wait = res.execute_after_ms > 0 ? `，約 ${res.execute_after_ms / 1000} 秒後執行` : ''
      toast(action === 'restart' ? `已排程重新啟動 ${name}${wait}` : `已送出 ${name}，等同 timer 觸發一次`)
      // 代理先回 202 再執行；等一下再重抓，才看得到新的 ActiveState／InvocationID。
      window.setTimeout(() => { void client.invalidateQueries({ queryKey: [...OPS_KEY] }) }, 3000)
    } catch (err) {
      if (isElevationCancelled(err)) return
      toast(messageOf(err), { tone: 'bad' })
    } finally {
      setBusy(false)
    }
  }

  const dialogs = (
    <>
      <ConfirmDialog
        open={pending !== null}
        tone={pending?.action === 'run' ? 'safe' : 'danger'}
        title={pending ? `${WRITE_ACTION_LABELS[pending.action]} ${pending.name}` : ''}
        body={pending?.action === 'restart'
          ? `確定要重新啟動「${pending.name}」？重啟期間服務會短暫中斷，這個頁面也會短暫連不上。`
          : `確定要立即執行「${pending?.name ?? ''}」一次？`}
        confirmLabel={pending ? WRITE_ACTION_LABELS[pending.action] : '確定'}
        onConfirm={() => { if (pending) void execute(pending) }}
        onCancel={() => setPending(null)}
      />
      {dialog}
    </>
  )
  return { canOperate, busy, request: (name, action) => setPending({ name, action }), dialogs }
}
