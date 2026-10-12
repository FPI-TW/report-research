import { Drawer, DrawerSection, Lamp } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import type { IncidentDetail, IncidentEventItem } from '../../../../lib/generated/adminApi'
import { fmtDateTime } from '../../auditLabels'
import { OpsQueryError } from '../OpsShared'
import { EVENT_ACTION_LABELS, componentLabel, fmtDuration } from '../opsLabels'
import { useOpsIncident } from '../useOps'
import { SeverityBadge, StatusBadge } from './IncidentBadges'
import { endText, eventTone } from './incidentText'
import styles from './Incidents.module.css'

function JournalExcerpt({ event }: { event: IncidentEventItem }) {
  if (!event.journal_excerpt) return null
  const range = event.journal_since && event.journal_until
    ? `${fmtDateTime(event.journal_since)} ～ ${fmtDateTime(event.journal_until)}`
    : null
  return (
    <details className={styles.journal}>
      <summary>
        journal 片段{range && `（${range}）`}
        {event.journal_units && <span> {event.journal_units}</span>}
      </summary>
      {event.journal_truncated && (
        <p className={styles.warnNote}>片段已截斷（有大小上限）；完整 log 請在主機上以 journalctl 查。</p>
      )}
      <pre className={`${c.log} ${styles.journalLog}`} aria-label="journal 片段" tabIndex={0}>{event.journal_excerpt}</pre>
    </details>
  )
}

function Detail({ incident }: { incident: IncidentDetail }) {
  return (
    <>
      <DrawerSection title="摘要">
        <dl className={c.kv}>
          <dt>元件</dt><dd>{componentLabel(incident.component)}（{incident.component}）</dd>
          <dt>嚴重度</dt><dd><SeverityBadge severity={incident.severity} /></dd>
          <dt>開始</dt><dd>{fmtDateTime(incident.opened_at)}</dd>
          <dt>結束</dt><dd>{endText(incident)}</dd>
          <dt>持續</dt><dd>{fmtDuration(incident.duration_seconds)}</dd>
          <dt>原因</dt><dd>{incident.reason}</dd>
          {incident.summary && <><dt>說明</dt><dd>{incident.summary}</dd></>}
          {incident.probe_unit && <><dt>探針</dt><dd>{incident.probe_unit}</dd></>}
          <dt>主機</dt><dd>{incident.host}</dd>
        </dl>
      </DrawerSection>
      <DrawerSection title="狀態轉換（舊→新）">
        {incident.events_truncated && <p className={styles.warnNote}>轉換太多，只顯示最早的 1000 則。</p>}
        <ol className={styles.timeline}>
          {incident.events.map(e => (
            <li key={e.event_id} className={styles.event}>
              <span className={styles.eventTime}>{fmtDateTime(e.occurred_at)}</span>
              <Lamp tone={eventTone(e)} label="" size={7} className={styles.eventDot} />
              <div>
                <div className={styles.eventHead}>
                  <span className={styles.eventAction} data-tone={eventTone(e)}>{EVENT_ACTION_LABELS[e.action]}</span>
                  <SeverityBadge severity={e.severity} />
                  <span>{e.notified ? '已通知' : '未送出通知'}</span>
                  <span className={styles.eventReason}>{e.reason}</span>
                </div>
                {e.summary && <p className={styles.eventSummary}>{e.summary}</p>}
                <JournalExcerpt event={e} />
              </div>
            </li>
          ))}
        </ol>
      </DrawerSection>
    </>
  )
}

/**
 * 事件詳情抽屜（520px，`?incident=<id>`）：摘要＋狀態轉換時間線（舊→新），每一則轉換可展開當時的 journal 片段
 * （預設收合、等寬、有截斷提示）。找不到的 id（404）在抽屜裡顯示後端的訊息。
 */
export function IncidentDrawer({ incidentId, onClose }: { incidentId: string | null; onClose: () => void }) {
  const q = useOpsIncident(incidentId ?? '')
  if (!incidentId) return null
  const i = q.data
  return (
    <Drawer open onClose={onClose} width={520}
      title={i ? `${componentLabel(i.component)} 事件` : '事件詳情'}
      status={i ? <StatusBadge status={i.status} /> : undefined}
      subtitle={i ? (i.summary ? `${i.reason}・${i.summary}` : i.reason) : undefined}>
      {q.isPending ? (
        <DrawerSection><p className={styles.note}>載入中…</p></DrawerSection>
      ) : q.isError ? (
        <DrawerSection><OpsQueryError error={q.error} what="事件詳情" /></DrawerSection>
      ) : (
        <Detail incident={q.data} />
      )}
    </Drawer>
  )
}
