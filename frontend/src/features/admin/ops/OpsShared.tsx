import { isAgentUnavailable } from './opsLabels'
import c from '../../../components/console/Console.module.css'
import { t } from '@/i18n'

/**
 * 維運 API 的錯誤：代理不可用（503 `ops_agent_unavailable`）時整頁換成明確的降級說明，
 * 其餘（403 缺 scope、404、502、504 逾時）原樣顯示後端的 detail。不白屏。
 */
export function OpsQueryError({ error, what }: { error: unknown; what: string }) {
  if (isAgentUnavailable(error)) {
    return (
      <section className={`${c.card} ${c.degraded}`} role="alert" aria-labelledby="ops-unavailable-title">
        <h2 id="ops-unavailable-title" className={c.cardTitle}>{t('admin.ops.opsShared.theOpsAgentIsCurrently')}</h2>
        <p>
          {t('admin.ops.opsShared.serviceStatusAndLogsAre')}
        </p>
        {error instanceof Error && error.message && <p className={c.degradedDetail}>{error.message}</p>}
        <p>
          {t('admin.ops.opsShared.onTheHostCheckWhether')}{' '}<code>report-mark-ops-agent.service</code>{' '}{t('admin.ops.opsShared.isRunningAndWhetherWeb')}{' '}<code>docs/production_resilience.md</code>{t('admin.ops.opsShared.opsAgent')}
        </p>
      </section>
    )
  }
  const msg = error instanceof Error && error.message ? error.message : t('admin.ops.opsShared.couldnTLoadTryAgain')
  return <p className={c.error} role="alert">{t('admin.ops.opsShared.couldnTLoad', { what, msg })}</p>
}
