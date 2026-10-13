import type { LampTone } from '../../../../components/console'
import type { IncidentEventItem, IncidentItem } from '../../../../lib/generated/adminApi'
import { fmtDateTime } from '../../auditLabels'

/** 結束欄：resolved 是恢復時間；lost 不知道何時結束，只能給最後一則轉換；firing 還沒結束。 */
export function endText(i: IncidentItem): string {
  if (i.status === 'resolved') return fmtDateTime(i.resolved_at)
  if (i.status === 'lost') return `不明（最後轉換 ${fmtDateTime(i.last_event_at)}）`
  return '—'
}

/** 事件狀態的燈號：進行中＝異常；已恢復＝正常；結束不明只知道它結束了，用中性色。 */
export const INCIDENT_STATUS_TONE: Record<IncidentItem['status'], LampTone> = {
  firing: 'bad',
  resolved: 'ok',
  lost: 'idle',
}

export const SEVERITY_TONE: Record<IncidentEventItem['severity'], LampTone> = {
  CRITICAL: 'bad',
  WARNING: 'warn',
  RESOLVED: 'ok',
}

/** 時間線上每一則轉換的顏色：開始與提醒跟著嚴重度，升級一律紅，恢復綠。 */
export function eventTone(e: IncidentEventItem): LampTone {
  if (e.action === 'RESOLVED') return 'ok'
  if (e.action === 'ESCALATED') return 'bad'
  return SEVERITY_TONE[e.severity]
}
