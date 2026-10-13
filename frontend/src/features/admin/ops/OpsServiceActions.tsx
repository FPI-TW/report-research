import { Button, DrawerFootNote } from '../../../components/console'
import type { OpsServiceStatus } from '../../../lib/generated/adminApi'
import { WRITE_ACTION_LABELS, useServiceActions, writeActionsOf } from './services/useServiceActions'
import styles from './services/Services.module.css'

/**
 * 詳情抽屜底部的動作列：一句權限說明＋「重新啟動」或「立即執行」。沒有 `ops.operate`、或 catalog 沒對這個
 * 服務開放寫入時什麼都不畫（抽屜也不留底列，見 `ServiceDrawer`）。
 */
export function OpsServiceActions({ service }: { service: Pick<OpsServiceStatus, 'name' | 'actions'> }) {
  const { canOperate, busy, request, dialogs } = useServiceActions()
  const actions = writeActionsOf(service)
  if (!canOperate || actions.length === 0) return null
  return (
    <>
      <DrawerFootNote>需要 ops.operate，執行前會要求重新驗證</DrawerFootNote>
      <div className={styles.footActions} role="group" aria-label="服務操作">
        {actions.map(a => (
          <Button key={a} variant="primary" disabled={busy} onClick={() => request(service.name, a)}>
            {WRITE_ACTION_LABELS[a]}
          </Button>
        ))}
      </div>
      {dialogs}
    </>
  )
}
