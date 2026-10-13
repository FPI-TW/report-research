import { isAgentUnavailable } from './opsLabels'
import c from '../../../components/console/Console.module.css'

/**
 * 維運 API 的錯誤：代理不可用（503 `ops_agent_unavailable`）時整頁換成明確的降級說明，
 * 其餘（403 缺 scope、404、502、504 逾時）原樣顯示後端的 detail。不白屏。
 */
export function OpsQueryError({ error, what }: { error: unknown; what: string }) {
  if (isAgentUnavailable(error)) {
    return (
      <section className={`${c.card} ${c.degraded}`} role="alert" aria-labelledby="ops-unavailable-title">
        <h2 id="ops-unavailable-title" className={c.cardTitle}>維運代理目前無法使用</h2>
        <p>
          暫時看不到服務狀態與日誌；研報平台的其他功能不受影響。
        </p>
        {error instanceof Error && error.message && <p className={c.degradedDetail}>{error.message}</p>}
        <p>
          請在主機上確認 <code>report-mark-ops-agent.service</code> 是否在跑、web 的執行身分能否連到它的 socket
          （步驟見 <code>docs/production_resilience.md</code>「維運代理」）。
        </p>
      </section>
    )
  }
  const msg = error instanceof Error && error.message ? error.message : '載入失敗，請重試'
  return <p className={c.error} role="alert">{what}載入失敗：{msg}</p>
}
