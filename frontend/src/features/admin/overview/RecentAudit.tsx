import { Link } from 'react-router'
import { Card } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { actionLabel, actorLabel, auditSummary, fmtDateTime } from '../auditLabels'
import { useRecentAudit } from './overviewData'
import styles from '../Overview.module.css'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '載入失敗，請重試'
}

/** 「最近操作」：操作紀錄最新的幾筆（同一支 API、同一組中文標籤），完整的在操作紀錄頁。 */
export function RecentAudit({ auditTo }: { auditTo: string }) {
  const q = useRecentAudit(true)
  return (
    <Card title="最近操作" labelledBy="overview-audit" right={<Link to={auditTo} className={c.link}>全部紀錄</Link>}>
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">操作紀錄載入失敗：{messageOf(q.error)}</p></div>
      ) : q.data.items.length === 0 ? (
        <p className={c.empty}>還沒有任何管理操作</p>
      ) : (
        <ul className={styles.recent}>
          {q.data.items.map(e => {
            const summary = auditSummary(e)
            const actor = actorLabel(e)
            const action = actionLabel(e.action)
            return (
              <li key={e.id} className={styles.recentRow}>
                <span className={styles.recentTime}>{fmtDateTime(e.created_at)}</span>
                <span className={styles.recentActor} data-muted={!e.actor_username || undefined} title={actor}>{actor}</span>
                <span className={styles.recentAction} title={action}>{action}</span>
                <span className={styles.recentSum} title={summary}>{summary}</span>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
