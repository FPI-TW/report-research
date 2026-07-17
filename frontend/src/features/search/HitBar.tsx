import { marketLabel } from '../../lib/meta'
import { Spectrum, type SpectrumSlice } from './Spectrum'
import styles from './HitBar.module.css'

interface Props {
  slices: SpectrumSlice[]
  total: number
}

/** 命中組成條：色譜重新分佈成「命中結果」的市場組成，不再是全語料庫。 */
export function HitBar({ slices, total }: Props) {
  const top = slices[0]
  const sum = slices.reduce((n, s) => n + s.count, 0)
  const pct = top && sum ? Math.round((top.count / sum) * 100) : 0

  return (
    <div className={styles.hitbar}>
      <Spectrum slices={slices} label="命中結果市場組成" className={styles.spectrum} />
      <span className={styles.read}>
        找到 <b>{total.toLocaleString()}</b> 篇
        {top && <> · {marketLabel(top.market)}佔 <span className={styles.gold}>{pct}%</span></>}
      </span>
    </div>
  )
}
