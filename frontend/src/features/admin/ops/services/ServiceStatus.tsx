import { Status } from '../../../../components/console'
import type { OpsServiceStatus } from '../../../../lib/generated/adminApi'
import { SUMMARY_LABELS } from '../opsLabels'
import { serviceTone } from './health'

/** 服務的整體狀態：燈號＋文字（燈號規則見 `serviceTone`）。 */
export function ServiceStatus({ service }: { service: OpsServiceStatus }) {
  return <Status tone={serviceTone(service)}>{SUMMARY_LABELS[service.summary]}</Status>
}
