import type { LampTone } from '../../../../components/console'
import type { DataHealthResponse } from '../../../../lib/generated/adminApi'

/** 資料健康各段與檢索回歸共用的四態（後端 `ok｜warn｜fail｜unknown`）。 */
export type HealthState = DataHealthResponse['overall']

export const HEALTH_LABELS: Record<HealthState, string> = { ok: '正常', warn: '注意', fail: '異常', unknown: '未知' }
export const HEALTH_TONE: Record<HealthState, LampTone> = { ok: 'ok', warn: 'warn', fail: 'bad', unknown: 'idle' }

/** 數字磚的字色：未知不上色（不是好也不是壞）。 */
export function healthTileTone(s: HealthState): 'ok' | 'warn' | 'bad' | undefined {
  return s === 'unknown' ? undefined : s === 'fail' ? 'bad' : s
}
