import { useState } from 'react'
import { adminCsvUrls, type IncidentDetail, type IncidentEventItem, type IncidentItem } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { ExportCsvButton } from '../ExportCsvButton'
import { OpsQueryError } from './OpsShared'
import {
  EVENT_ACTION_LABELS, INCIDENT_COMPONENTS, INCIDENT_STATUS_HINTS, INCIDENT_STATUS_LABELS, componentLabel, fmtDuration,
} from './opsLabels'
import { INCIDENTS_PAGE_SIZE, useOpsIncident, useOpsIncidents } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

type IncidentStatus = IncidentItem['status']

const STATUS_OPTIONS: { value: IncidentStatus | ''; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'firing', label: INCIDENT_STATUS_LABELS.firing },
  { value: 'resolved', label: INCIDENT_STATUS_LABELS.resolved },
  { value: 'lost', label: INCIDENT_STATUS_LABELS.lost },
]

const STATUS_CLASS: Record<IncidentStatus, string> = {
  firing: styles.sFailed,
  resolved: styles.sRunning,
  lost: styles.sIdle,
}

function StatusBadge({ status }: { status: IncidentStatus }) {
  return (
    <span className={`${styles.pill} ${STATUS_CLASS[status]}`} title={INCIDENT_STATUS_HINTS[status]}>
      {INCIDENT_STATUS_LABELS[status]}
    </span>
  )
}

function SeverityBadge({ severity }: { severity: IncidentEventItem['severity'] }) {
  const cls = severity === 'CRITICAL' ? styles.sFailed : severity === 'WARNING' ? styles.sTransitioning : styles.sRunning
  return <span className={`${styles.pill} ${cls}`}>{severity}</span>
}

/** 結束欄：resolved 是恢復時間；lost 不知道何時結束，只能給最後一則轉換；firing 還沒結束。 */
function endText(i: IncidentItem): string {
  if (i.status === 'resolved') return fmtDateTime(i.resolved_at)
  if (i.status === 'lost') return `不明（最後轉換 ${fmtDateTime(i.last_event_at)}）`
  return '—'
}

function JournalExcerpt({ event }: { event: IncidentEventItem }) {
  if (!event.journal_excerpt) return null
  const range = event.journal_since && event.journal_until
    ? `${fmtDateTime(event.journal_since)} ～ ${fmtDateTime(event.journal_until)}`
    : null
  return (
    <details className={styles.journal}>
      <summary>
        journal 片段{range && `（${range}）`}
        {event.journal_units && <span className={adminStyles.muted}> {event.journal_units}</span>}
      </summary>
      {event.journal_truncated && (
        <p className={styles.warnNote}>片段已截斷（有大小上限）；完整 log 請在主機上以 journalctl 查。</p>
      )}
      <pre className={styles.logBox} aria-label="journal 片段">{event.journal_excerpt}</pre>
    </details>
  )
}

function IncidentDetailCard({ incident }: { incident: IncidentDetail }) {
  return (
    <>
      <dl className={styles.dl}>
        <dt>元件</dt><dd>{componentLabel(incident.component)}（{incident.component}）</dd>
        <dt>狀態</dt><dd><StatusBadge status={incident.status} /></dd>
        <dt>嚴重度</dt><dd><SeverityBadge severity={incident.severity} /></dd>
        <dt>開始</dt><dd>{fmtDateTime(incident.opened_at)}</dd>
        <dt>結束</dt><dd>{endText(incident)}</dd>
        <dt>持續</dt><dd>{fmtDuration(incident.duration_seconds)}</dd>
        <dt>原因</dt><dd>{incident.reason}</dd>
        {incident.summary && <><dt>說明</dt><dd>{incident.summary}</dd></>}
        {incident.probe_unit && <><dt>探針</dt><dd>{incident.probe_unit}</dd></>}
        <dt>主機</dt><dd>{incident.host}</dd>
      </dl>
      <div className={adminStyles.spacer} />
      <h3 className={styles.groupTitle}>狀態轉換（舊→新）</h3>
      {incident.events_truncated && <p className={styles.warnNote}>轉換太多，只顯示最早的 1000 則。</p>}
      <ol className={styles.events}>
        {incident.events.map(e => (
          <li key={e.event_id} className={styles.event}>
            <div className={styles.eventHead}>
              <b>{EVENT_ACTION_LABELS[e.action]}</b>
              <SeverityBadge severity={e.severity} />
              <span className={adminStyles.num}>{fmtDateTime(e.occurred_at)}</span>
              <span className={adminStyles.muted}>{e.notified ? '已通知' : '未送出通知'}</span>
              <span className={adminStyles.muted}>{e.reason}</span>
            </div>
            {e.summary && <p className={styles.eventSummary}>{e.summary}</p>}
            <JournalExcerpt event={e} />
          </li>
        ))}
      </ol>
    </>
  )
}

function IncidentDetailPanel({ incidentId, onClose }: { incidentId: string; onClose: () => void }) {
  const q = useOpsIncident(incidentId)
  return (
    <section className={adminStyles.card} aria-labelledby="ops-incident-detail-title">
      <h2 id="ops-incident-detail-title" className={adminStyles.ctitle}>事件詳情</h2>
      {q.isPending ? (
        <p className={adminStyles.idle}>載入中…</p>
      ) : q.isError ? (
        <OpsQueryError error={q.error} what="事件詳情" />
      ) : (
        <IncidentDetailCard incident={q.data} />
      )}
      <div className={styles.links}>
        <button type="button" className={adminStyles.action} onClick={onClose}>關閉詳情</button>
      </div>
    </section>
  )
}

/**
 * 事件：主機上的事件偵測（P5）每次開事件、提醒、升級、恢復的紀錄（新→舊）。資料是 DB 投影（P5 寫本機 spool、
 * loader 每 5 分鐘匯入），**不是告警的真相來源**——即時告警看 Slack；DB 掛掉期間的事件恢復後補進來。
 */
export default function OpsIncidentsPage() {
  const [status, setStatus] = useState<IncidentStatus | ''>('')
  const [component, setComponent] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState('')
  const q = useOpsIncidents(status, component, offset)

  return (
    <>
      <section className={adminStyles.card} aria-labelledby="ops-incidents-title">
        <div className={adminStyles.cardHead}>
          <h2 id="ops-incidents-title" className={adminStyles.ctitle}>事件</h2>
          <ExportCsvButton
            href={adminCsvUrls.exportIncidents({ status: status || undefined, component: component || undefined })}
            what="事件清單"
          />
        </div>
        <div className={adminStyles.form}>
          <label className={adminStyles.field}>
            狀態
            <select value={status} onChange={e => { setStatus(e.target.value as IncidentStatus | ''); setOffset(0) }}>
              {STATUS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className={adminStyles.field}>
            元件
            <select value={component} onChange={e => { setComponent(e.target.value); setOffset(0) }}>
              <option value="">全部</option>
              {INCIDENT_COMPONENTS.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </label>
        </div>
        <div className={adminStyles.spacer} />
        {q.isPending ? (
          <p className={adminStyles.idle}>載入中…</p>
        ) : q.isError ? (
          <OpsQueryError error={q.error} what="事件" />
        ) : (
          <>
            <p className={styles.meta}>
              <span>範圍 <b>{fmtDateTime(q.data.since)}</b> ～ <b>{fmtDateTime(q.data.until)}</b></span>
              <span>共 <b>{q.data.total}</b> 筆（每 5 分鐘匯入一次；即時告警以 Slack 為準）</span>
            </p>
            <div className={adminStyles.spacer} />
            {q.data.items.length === 0 ? (
              <p className={adminStyles.idle}>
                這段期間沒有事件。若主機上確實發過告警卻一直是空的，確認匯入器
                （report-mark-load-observations.timer）在跑、spool 目錄 data/ops_spool/ 可寫。
              </p>
            ) : (
              <div className={adminStyles.tableWrap}>
                <table className={adminStyles.table} aria-label="事件清單">
                  <thead>
                    <tr><th>元件</th><th>狀態</th><th>嚴重度</th><th>開始</th><th>結束</th><th>持續</th><th>原因</th><th /></tr>
                  </thead>
                  <tbody>
                    {q.data.items.map(i => (
                      <tr key={i.incident_id}>
                        <td className={adminStyles.wrapCell}>
                          {componentLabel(i.component)}
                          <div className={adminStyles.muted}>{i.kind === 'monitor_blind' ? '監控失明' : i.component}</div>
                        </td>
                        <td><StatusBadge status={i.status} /></td>
                        <td><SeverityBadge severity={i.severity} /></td>
                        <td className={adminStyles.num}>{fmtDateTime(i.opened_at)}</td>
                        <td className={adminStyles.num}>{endText(i)}</td>
                        <td className={adminStyles.num}>{fmtDuration(i.duration_seconds)}</td>
                        <td className={adminStyles.wrapCell}>
                          {i.reason}
                          {i.summary && <div className={adminStyles.muted}>{i.summary}</div>}
                        </td>
                        <td>
                          <button
                            type="button"
                            className={adminStyles.action}
                            aria-pressed={selected === i.incident_id}
                            onClick={() => setSelected(selected === i.incident_id ? '' : i.incident_id)}
                          >
                            詳情
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {(offset > 0 || q.data.has_more) && (
              <div className={adminStyles.pager}>
                <button
                  type="button"
                  className={adminStyles.action}
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - INCIDENTS_PAGE_SIZE))}
                >
                  較新
                </button>
                <span>第 {offset + 1}–{offset + q.data.items.length} 筆</span>
                <button
                  type="button"
                  className={adminStyles.action}
                  disabled={!q.data.has_more}
                  onClick={() => setOffset(q.data.next_offset ?? offset)}
                >
                  較舊
                </button>
              </div>
            )}
          </>
        )}
      </section>
      {selected && <IncidentDetailPanel incidentId={selected} onClose={() => setSelected('')} />}
    </>
  )
}
