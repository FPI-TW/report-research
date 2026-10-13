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

type IncidentStatus = IncidentItem['status']
type StatusFilter = IncidentStatus | 'all'

const STATUS_OPTIONS: SegOption<StatusFilter>[] = [
  { value: 'all', label: '全部' },
  { value: 'firing', label: INCIDENT_STATUS_LABELS.firing },
  { value: 'resolved', label: INCIDENT_STATUS_LABELS.resolved },
  { value: 'lost', label: INCIDENT_STATUS_LABELS.lost },
]

const HELP = (
  <>
    主機上的事件偵測每次開事件、提醒、升級、恢復的紀錄（新→舊）。資料是 DB 投影（偵測端寫本機 spool、匯入器每 5 分鐘
    匯入），不是告警的真相來源——即時告警看 Slack；DB 掛掉期間的事件恢復後補進來。
    「結束不明」＝沒收到「已恢復」，但同一個元件之後又開了新事件：它必定已經結束，只是不知道何時。
  </>
)

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
          ? `${q.data.total} 筆事件；每 5 分鐘匯入一次，即時告警以 Slack 為準。`
          : '主機事件偵測的紀錄；即時告警以 Slack 為準。'}
        help={HELP}
        actions={(
          <>
            <Segmented label="狀態" options={STATUS_OPTIONS} value={filter}
              onChange={v => { setFilter(v); setOffset(0) }} />
            <ExportCsvButton
              href={adminCsvUrls.exportIncidents({ status: status || undefined, component: component || undefined })}
              what="事件清單"
            />
          </>
        )}
      />
      <section className={c.card} aria-labelledby="ops-incidents-title">
        <div className={c.cardHead}>
          <h2 id="ops-incidents-title" className={c.cardTitle}>事件清單</h2>
          {q.data && <span className={c.cardHint}>{fmtDateTime(q.data.since)} ～ {fmtDateTime(q.data.until)}</span>}
          <div className={c.cardRight}>
            <label className={styles.inlineField}>
              元件
              <select className={c.select} value={component} onChange={e => { setComponent(e.target.value); setOffset(0) }}>
                <option value="">全部</option>
                {INCIDENT_COMPONENTS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
              </select>
            </label>
          </div>
        </div>
        {q.isPending ? (
          <p className={c.empty}>載入中…</p>
        ) : q.isError ? (
          <div className={c.cardBody}><OpsQueryError error={q.error} what="事件" /></div>
        ) : (
          <>
            {q.data.items.length === 0 ? (
              <p className={c.empty}>
                這段期間沒有事件。若主機上確實發過告警卻一直是空的，確認匯入器
                （report-mark-load-observations.timer）在跑、spool 目錄 data/ops_spool/ 可寫。
              </p>
            ) : (
              <div className={c.tableWrap}>
                <table className={c.table} aria-label="事件清單">
                  <thead>
                    <tr>
                      <th>元件</th><th>狀態</th><th>嚴重度</th><th>開始</th><th>結束</th><th>持續</th><th>原因</th>
                      <th className={`${c.tdRight} ${c.stickyRight}`}>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {q.data.items.map(i => (
                      <tr key={i.incident_id}
                        className={`${c.rowClickable} ${incident === i.incident_id ? styles.rowOpen : ''}`}
                        onClick={() => setIncident(i.incident_id)}>
                        <td>
                          {componentLabel(i.component)}
                          <div className={c.sub}>{i.kind === 'monitor_blind' ? '監控失明' : i.component}</div>
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
                          <RowActions primary={{ label: '詳情', onClick: () => setIncident(i.incident_id) }} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {(offset > 0 || q.data.has_more) && (
              <div className={c.pager}>
                <span>第 {offset + 1}–{offset + q.data.items.length} 筆，共 {q.data.total} 筆</span>
                <div className={c.pagerBtns}>
                  <Button size="sm" disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - INCIDENTS_PAGE_SIZE))}>
                    較新
                  </Button>
                  <Button size="sm" disabled={!q.data.has_more} onClick={() => setOffset(q.data.next_offset ?? offset)}>
                    較舊
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
