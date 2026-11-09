import { useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { ConfirmDialog, isElevationCancelled, toast, useElevate } from '../../../../components/console'
import { ApiError } from '../../../../lib/api'
import { adminApi, type OpsServiceStatus } from '../../../../lib/generated/adminApi'
import { useHasScope } from '../../../../lib/useMe'
import { OPS_KEY } from '../useOps'
import { t } from '@/i18n'

export type WriteAction = 'restart' | 'run'

export const WRITE_ACTION_LABELS: Record<WriteAction, string> = { get restart() { return t('admin.ops.services.useServiceActions.restart') }, get run() { return t('admin.ops.services.useServiceActions.runNow') } }

/** catalog 對這個服務開放的寫入類操作（白名單在主機上的 Service Catalog；這裡只是照著顯示）。 */
export function writeActionsOf(service: Pick<OpsServiceStatus, 'actions'>): WriteAction[] {
  return service.actions.filter((a): a is WriteAction => a === 'restart' || a === 'run')
}

/** 後端錯誤代碼 → 給人看的一句話；其餘沿用後端 detail（requestJSON 已放進 message）。 */
function messageOf(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'already_running') return t('admin.ops.services.useServiceActions.theSameJobIsAlready')
    if (err.code === 'action_not_allowed') return t('admin.ops.services.useServiceActions.thisServiceDoesnTAllow')
    if (err.code === 'ops_agent_unavailable') return t('admin.ops.services.useServiceActions.theOpsAgentIsCurrently')
    if (err.code === 'missing_scope') return t('admin.ops.services.useServiceActions.requiresTheOpsOperatePermission')
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
      const wait = res.execute_after_ms > 0 ? t('admin.ops.services.useServiceActions.runningInAboutS', { v: res.execute_after_ms / 1000 }) : ''
      toast(action === 'restart' ? t('admin.ops.services.useServiceActions.restartOfScheduled', { name, wait }) : t('admin.ops.services.useServiceActions.sentSameAsOneTimer', { name }))
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
          ? t('admin.ops.services.useServiceActions.restartTheServiceIsBriefly', { name: pending.name })
          : t('admin.ops.services.useServiceActions.runOnceNow', { v: pending?.name ?? '' })}
        confirmLabel={pending ? WRITE_ACTION_LABELS[pending.action] : t('admin.ops.services.useServiceActions.confirm')}
        onConfirm={() => { if (pending) void execute(pending) }}
        onCancel={() => setPending(null)}
      />
      {dialog}
    </>
  )
  return { canOperate, busy, request: (name, action) => setPending({ name, action }), dialogs }
}
