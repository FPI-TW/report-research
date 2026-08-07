import type { InstrumentStance } from '../../lib/radarSchemas'
import {
  BUCKET_DISPLAY, BUCKET_MEMBERS, BUCKET_ORDER, bucketSlices, RATING_DISPLAY,
  RATING_SAMPLE_MIN, RATING_SAMPLE_NOTE,
} from './radarFormat'
import styles from './RatingDistBar.module.css'

interface Props {
  stance: InstrumentStance
}

/**
 * 清單頁的評等分布：三桶比例條 ＋ 同一組數字的文字圖例。
 *
 * **刻意壓成三桶而非五級**（詳情頁的 `ConsensusSnapshot` 才是五級）：一列的高度只夠
 * 一組結論，五段細分在 8px 高的條上分不出「加碼」與「買進」，而文字圖例列到五項就會
 * 換到第三行。五級明細沒有消失——它進了 `title` 與可及名稱，滑鼠與讀屏都拿得回來。
 *
 * 條與圖例是同一份整數百分比（`bucketSlices` 保證加總 100），所以「條看起來的比例」
 * 與「文字寫的百分比」不可能各說各話。
 */
export function RatingDistBar({ stance }: Props) {
  const slices = bucketSlices(stance)
  const total = stance.total_rated

  if (!slices.length) {
    return <span className={styles.empty}>尚無評等</span>
  }

  const counts = new Map(stance.distribution.map(d => [d.rating, d.count]))
  // 五級明細：只列真的有家數的級別，順序沿用偏多→偏空。
  const detail = BUCKET_ORDER
    .flatMap(b => BUCKET_MEMBERS[b])
    .filter(r => (counts.get(r) ?? 0) > 0)
    .map(r => `${RATING_DISPLAY[r]} ${counts.get(r)} 家`)
    .join('、')
  const summary = slices
    .map(s => `${BUCKET_DISPLAY[s.bucket]} ${s.pct}%，${s.count} 家`)
    .join('、')
  const lowSample = total > 0 && total < RATING_SAMPLE_MIN

  return (
    // role="img" ＋ 完整 aria-label：底下的條與圖例是同一份數字的兩種畫法，
    // 讓讀屏各讀一次等於把同一件事說兩遍。明細與樣本提醒都併進這個名字裡。
    <div
      className={styles.wrap}
      role="img"
      aria-label={
        `評等分布，共 ${total} 家已評等：${summary}`
        + (detail ? `。五級明細：${detail}` : '')
        + (lowSample ? `。${RATING_SAMPLE_NOTE}` : '')
      }
      title={detail || undefined}
    >
      <div className={styles.bar}>
        {slices.map(s => (
          <span
            key={s.bucket}
            className={styles[s.bucket]}
            style={{ width: `${s.pct}%` }}
          />
        ))}
      </div>
      <div className={styles.legend}>
        {slices.map(s => (
          <span key={s.bucket} className={styles.item}>
            <i className={`${styles.dot} ${styles[s.bucket]}`} />
            {BUCKET_DISPLAY[s.bucket]}
            <b className={styles.pct}>{s.pct}%</b>
            {/* 半形括號：全形一組多吃約 12px，而那正好讓一行從兩項退化成一項。 */}
            <span className={styles.n}>({s.count})</span>
          </span>
        ))}
        {lowSample ? <span className={styles.low}>樣本不足</span> : null}
      </div>
    </div>
  )
}
