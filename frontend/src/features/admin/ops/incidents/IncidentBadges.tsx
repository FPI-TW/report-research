import { Chip, Status } from '../../../../components/console'
import type { IncidentEventItem, IncidentItem } from '../../../../lib/generated/adminApi'
import { INCIDENT_STATUS_HINTS, INCIDENT_STATUS_LABELS } from '../opsLabels'
import { INCIDENT_STATUS_TONE, SEVERITY_TONE } from './incidentText'

/** 事件狀態：燈號＋文字；滑過去有這個狀態的說明（例如「結束不明」是怎麼判定的）。 */
export function StatusBadge({ status }: { status: IncidentItem['status'] }) {
  return (
    <span title={INCIDENT_STATUS_HINTS[status]}>
      <Status tone={INCIDENT_STATUS_TONE[status]}>{INCIDENT_STATUS_LABELS[status]}</Status>
    </span>
  )
}

export function SeverityBadge({ severity }: { severity: IncidentEventItem['severity'] }) {
  return <Chip tone={SEVERITY_TONE[severity]} mono>{severity}</Chip>
}
