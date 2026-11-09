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
import { t } from '@/i18n'

function JournalExcerpt({ event }: { event: IncidentEventItem }) {
  if (!event.journal_excerpt) return null
  const range = event.journal_since && event.journal_until
    ? t('admin.ops.incidents.incidentDrawer.text', { journal_since: fmtDateTime(event.journal_since), journal_until: fmtDateTime(event.journal_until) })
    : null
  return (
    <details className={styles.journal}>
      <summary>
        {t('admin.ops.incidents.incidentDrawer.journalExcerpt')}{range && t('admin.ops.incidents.incidentDrawer.text2', { range })}
        {event.journal_units && <span> {event.journal_units}</span>}
      </summary>
      {event.journal_truncated && (
        <p className={styles.warnNote}>{t('admin.ops.incidents.incidentDrawer.excerptTruncatedSizeLimitQuery')}</p>
      )}
      <pre className={`${c.log} ${styles.journalLog}`} aria-label={t('admin.ops.incidents.incidentDrawer.journalExcerpt')} tabIndex={0}>{event.journal_excerpt}</pre>
    </details>
  )
}

function Detail({ incident }: { incident: IncidentDetail }) {
  return (
    <>
      <DrawerSection title={t('admin.ops.incidents.incidentDrawer.summary')}>
        <dl className={c.kv}>
          <dt>{t('admin.ops.incidents.incidentDrawer.component')}</dt><dd>{t('admin.ops.incidents.incidentDrawer.text3', { component: componentLabel(incident.component), component2: incident.component })}</dd>
          <dt>{t('admin.ops.incidents.incidentDrawer.severity')}</dt><dd><SeverityBadge severity={incident.severity} /></dd>
          <dt>{t('admin.ops.incidents.incidentDrawer.started')}</dt><dd>{fmtDateTime(incident.opened_at)}</dd>
          <dt>{t('admin.ops.incidents.incidentDrawer.ended')}</dt><dd>{endText(incident)}</dd>
          <dt>{t('admin.ops.incidents.incidentDrawer.duration')}</dt><dd>{fmtDuration(incident.duration_seconds)}</dd>
          <dt>{t('admin.ops.incidents.incidentDrawer.reason')}</dt><dd>{incident.reason}</dd>
          {incident.summary && <><dt>{t('admin.ops.incidents.incidentDrawer.description')}</dt><dd>{incident.summary}</dd></>}
          {incident.probe_unit && <><dt>{t('admin.ops.incidents.incidentDrawer.probe')}</dt><dd>{incident.probe_unit}</dd></>}
          <dt>{t('admin.ops.incidents.incidentDrawer.host')}</dt><dd>{incident.host}</dd>
        </dl>
      </DrawerSection>
      <DrawerSection title={t('admin.ops.incidents.incidentDrawer.stateTransitionsOldNew')}>
        {incident.events_truncated && <p className={styles.warnNote}>{t('admin.ops.incidents.incidentDrawer.tooManyTransitionsOnlyThe')}</p>}
        <ol className={styles.timeline}>
          {incident.events.map(e => (
            <li key={e.event_id} className={styles.event}>
              <span className={styles.eventTime}>{fmtDateTime(e.occurred_at)}</span>
              <Lamp tone={eventTone(e)} label="" size={7} className={styles.eventDot} />
              <div>
                <div className={styles.eventHead}>
                  <span className={styles.eventAction} data-tone={eventTone(e)}>{EVENT_ACTION_LABELS[e.action]}</span>
                  <SeverityBadge severity={e.severity} />
                  <span>{e.notified ? t('admin.ops.incidents.incidentDrawer.notified') : t('admin.ops.incidents.incidentDrawer.notificationNotSent')}</span>
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
      title={i ? t('admin.ops.incidents.incidentDrawer.incident', { component: componentLabel(i.component) }) : t('admin.ops.incidents.incidentDrawer.incidentDetails')}
      status={i ? <StatusBadge status={i.status} /> : undefined}
      subtitle={i ? (i.summary ? `${i.reason}${t('common.separator')}${i.summary}` : i.reason) : undefined}>
      {q.isPending ? (
        <DrawerSection><p className={styles.note}>{t('admin.ops.incidents.incidentDrawer.loading')}</p></DrawerSection>
      ) : q.isError ? (
        <DrawerSection><OpsQueryError error={q.error} what={t('admin.ops.incidents.incidentDrawer.incidentDetails')} /></DrawerSection>
      ) : (
        <Detail incident={q.data} />
      )}
    </Drawer>
  )
}
