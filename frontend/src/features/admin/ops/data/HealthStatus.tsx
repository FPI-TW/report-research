import { Status } from '../../../../components/console'
import { HEALTH_LABELS, HEALTH_TONE, type HealthState } from './healthLabels'

/** 卡片標題列右側的狀態：燈號＋「正常／注意／異常／未知」。 */
export function HealthStatus({ status }: { status: HealthState }) {
  return <Status tone={HEALTH_TONE[status]}>{HEALTH_LABELS[status]}</Status>
}
