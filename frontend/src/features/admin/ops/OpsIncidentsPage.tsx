import { useState } from 'react'
import { Button, PageHeader, RowActions, Segmented, useQueryParam, type SegOption } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { adminCsvUrls, type IncidentItem } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { ExportCsvButton } from '../ExportCsvButton'
import { SeverityBadge, StatusBadge } from './incidents/IncidentBadges'
import { IncidentDrawer } from './incidents/IncidentDrawer'
import { IncidentTrendsSection } from './incidents/IncidentTrends'
import { endText } from './incidents/incidentText'
import { OpsQueryError } from './OpsShared'
import { INCIDENT_COMPONENTS, INCIDENT_STATUS_LABELS, componentLabel, fmtDuration } from './opsLabels'
import { INCIDENTS_PAGE_SIZE, useOpsIncidents } from './useOps'
import styles from './incidents/Incidents.module.css'
import { t } from '@/i18n'

type IncidentStatus = IncidentItem['status']
type StatusFilter = IncidentStatus | 'all'

const STATUS_OPTIONS: SegOption<StatusFilter>[] = [
  { value: 'all', get label() { return t('admin.ops.opsIncidentsPage.all') } },
  { value: 'firing', get label() { return INCIDENT_STATUS_LABELS.firing } },
  { value: 'resolved', get label() { return INCIDENT_STATUS_LABELS.resolved } },
  { value: 'lost', get label() { return INCIDENT_STATUS_LABELS.lost } },
]

const help = () => t('admin.ops.opsIncidentsPage.everyOpenReminderEscalationAnd')

/**
 * 事件：主機上的事件偵測（P5）每次開事件、提醒、升級、恢復的紀錄（新→舊）。資料是 DB 投影（P5 寫本機 spool、
 * loader 每 5 分鐘匯入），**不是告警的真相來源**——即時告警看 Slack；DB 掛掉期間的事件恢復後補進來。
 * 點列開詳情抽屜（`?incident=<id>`）；狀態與元件篩選、分頁留在頁面自己的狀態裡。
 */
export default function OpsIncidentsPage() {
  const [filter, setFilter] = useState<StatusFilter>('all')
  const [component, setComponent] = useState('')
  const [offset, setOffset] = useState(0)
  const [incident, setIncident] = useQueryParam('incident')
  const status: IncidentStatus | '' = filter === 'all' ? '' : filter
  const q = useOpsIncidents(status, component, offset)

  return (
    <>
      <PageHeader
        summary={q.data
          ? t('admin.ops.opsIncidentsPage.incidentsImportedEvery5Minutes', { total: q.data.total })
          : t('admin.ops.opsIncidentsPage.hostIncidentDetectionRecordsSlack')}
        help={help()}
        actions={(
          <>
            <Segmented label={t('admin.ops.opsIncidentsPage.status')} options={STATUS_OPTIONS} value={filter}
              onChange={v => { setFilter(v); setOffset(0) }} />
            <ExportCsvButton
              href={adminCsvUrls.exportIncidents({ status: status || undefined, component: component || undefined })}
              what={t('admin.ops.opsIncidentsPage.incidentList')}
            />
          </>
        )}
      />
      <section className={c.card} aria-labelledby="ops-incidents-title">
        <div className={c.cardHead}>
          <h2 id="ops-incidents-title" className={c.cardTitle}>{t('admin.ops.opsIncidentsPage.incidentList')}</h2>
          {q.data && <span className={c.cardHint}>{t('admin.ops.opsIncidentsPage.text', { since: fmtDateTime(q.data.since), until: fmtDateTime(q.data.until) })}</span>}
          <div className={c.cardRight}>
            <label className={styles.inlineField}>
              {t('admin.ops.opsIncidentsPage.component')}
              <select className={c.select} value={component} onChange={e => { setComponent(e.target.value); setOffset(0) }}>
                <option value="">{t('admin.ops.opsIncidentsPage.all')}</option>
                {INCIDENT_COMPONENTS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
              </select>
            </label>
          </div>
        </div>
        {q.isPending ? (
          <p className={c.empty}>{t('admin.ops.opsIncidentsPage.loading')}</p>
        ) : q.isError ? (
          <div className={c.cardBody}><OpsQueryError error={q.error} what={t('admin.ops.opsIncidentsPage.incidents')} /></div>
        ) : (
          <>
            {q.data.items.length === 0 ? (
              <p className={c.empty}>
                {t('admin.ops.opsIncidentsPage.noIncidentsInThisPeriod')}
              </p>
            ) : (
              <div className={c.tableWrap}>
                <table className={c.table} aria-label={t('admin.ops.opsIncidentsPage.incidentList')}>
                  <thead>
                    <tr>
                      <th>{t('admin.ops.opsIncidentsPage.component')}</th><th>{t('admin.ops.opsIncidentsPage.status')}</th><th>{t('admin.ops.opsIncidentsPage.severity')}</th><th>{t('admin.ops.opsIncidentsPage.started')}</th><th>{t('admin.ops.opsIncidentsPage.ended')}</th><th>{t('admin.ops.opsIncidentsPage.duration')}</th><th>{t('admin.ops.opsIncidentsPage.reason')}</th>
                      <th className={`${c.tdRight} ${c.stickyRight}`}>{t('admin.ops.opsIncidentsPage.actions')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {q.data.items.map(i => (
                      <tr key={i.incident_id}
                        className={`${c.rowClickable} ${incident === i.incident_id ? styles.rowOpen : ''}`}
                        onClick={() => setIncident(i.incident_id)}>
                        <td>
                          {componentLabel(i.component)}
                          <div className={c.sub}>{i.kind === 'monitor_blind' ? t('admin.ops.opsIncidentsPage.monitoringBlind') : i.component}</div>
                        </td>
                        <td><StatusBadge status={i.status} /></td>
                        <td><SeverityBadge severity={i.severity} /></td>
                        <td className={c.num}>{fmtDateTime(i.opened_at)}</td>
                        {/* 「不明（最後轉換 …）」很長：這一欄允許折行，寬度才留得下來給原因。 */}
                        <td className={`${c.mono} ${styles.end}`}>{endText(i)}</td>
                        <td className={c.num}>{fmtDuration(i.duration_seconds)}</td>
                        <td className={styles.reason}>
                          <span className={`${c.mono} ${c.nowrap}`}>{i.reason}</span>
                          {i.summary && <div className={c.sub}>{i.summary}</div>}
                        </td>
                        <td className={`${c.tdActions} ${c.stickyRight}`}>
                          <RowActions primary={{ label: t('admin.ops.opsIncidentsPage.details'), onClick: () => setIncident(i.incident_id) }} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {(offset > 0 || q.data.has_more) && (
              <div className={c.pager}>
                <span>{t('admin.ops.opsIncidentsPage.of', { v: offset + 1, v2: offset + q.data.items.length, total: q.data.total })}</span>
                <div className={c.pagerBtns}>
                  <Button size="sm" disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - INCIDENTS_PAGE_SIZE))}>
                    {t('admin.ops.opsIncidentsPage.newer')}
                  </Button>
                  <Button size="sm" disabled={!q.data.has_more} onClick={() => setOffset(q.data.next_offset ?? offset)}>
                    {t('admin.ops.opsIncidentsPage.older')}
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </section>
      <IncidentTrendsSection />
      <IncidentDrawer incidentId={incident} onClose={() => setIncident(null)} />
    </>
  )
}
