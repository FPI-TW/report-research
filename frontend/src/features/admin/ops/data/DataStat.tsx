import type { ReactNode } from 'react'
import { StatTile, type LampTone } from '../../../../components/console'
import styles from './Data.module.css'

/**
 * 數字磚：整塊是一個具名的 group（`name` 是無障礙名稱；畫面上的標籤預設同名，同一頁有兩組同名的磚時另給 `label`）。
 * 大數字與單位分開排（「9.0」＋「GiB」）；沒有值（「—」）時不帶單位。
 */
export function DataStat({ name, label = name, value, unit, hint, tone, lamp }: {
  name: string; label?: string; value: ReactNode; unit?: string; hint?: string; tone?: 'ok' | 'warn' | 'bad'; lamp?: LampTone
}) {
  return (
    <div className={styles.tileGroup} role="group" aria-label={name}>
      <StatTile label={label} value={value} unit={value === '—' ? undefined : unit}
        sub={hint && <span title={hint}>{hint}</span>} tone={tone} lamp={lamp} />
    </div>
  )
}
