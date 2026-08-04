import type { Signal } from '../../lib/readingSchemas'
import {
  RATING_BUCKET,
  RATING_DISPLAY,
  THESIS_ORDER,
  THESIS_LABEL,
  fmtTargetPrice,
  stanceDisplay,
  stanceTone,
  targetSub,
} from './readingFormat'
import styles from './SignalCard.module.css'

interface Props {
  signal: Signal
}

/** 語意桶 → CSS Module 類名。r-/s- 前綴避免評等色與論點色調在同一 module 內互相覆蓋。 */
const RATING_CLASS = { bull: 'rBull', neu: 'rNeu', bear: 'rBear' } as const
const TONE_CLASS = { pos: 'sPos', neu: 'sNeu', neg: 'sNeg' } as const

/**
 * 單一標的的結構化訊號：標的抬頭 + 評等/目標價雙格 + 四維論點。
 * 呼叫端負責 signals_state === 'none' 時整區不進 DOM。
 *
 * **抬頭不是裝飾**：一份研報可能同時對多檔標的有訊號（一列＝一份研報 × 一個標的），
 * 少了它就只剩一疊看不出在講誰的評等與目標價。名稱缺值（後端解析不出公司名）是常態，
 * 回退成把代號當主標，比照 displayTitle 的 title → file_name。
 */
export function SignalCard({ signal }: Props) {
  const rating = signal.rating_normalized
  const sub = targetSub(signal.target_currency, signal.target_horizon)
  const name = signal.instrument_name?.trim() || null

  // 後端可能回未定義順序的四維；依 THESIS_ORDER 對齊，缺的維度不補空列。
  const thesis = THESIS_ORDER.map(key => signal.thesis.find(t => t.key === key)).filter(
    (t): t is NonNullable<typeof t> => Boolean(t),
  )

  return (
    <div className={styles.card}>
      {/* 名稱當主標、代號當副標（沿用雷達 InstrumentCard 的次序）。名稱缺值時代號升為
          主標，而不是留一個空主標再把代號印在旁邊。 */}
      <div className={styles.head}>
        <h3 className={styles.name}>{name ?? signal.instrument_code}</h3>
        {name && <span className={styles.code}>{signal.instrument_code}</span>}
      </div>

      <div className={styles.figs}>
        <div className={styles.fig}>
          <div className={styles.figK}>評等</div>
          <div className={`${styles.figV} ${styles[RATING_CLASS[RATING_BUCKET[rating]]]}`}>
            {RATING_DISPLAY[rating]}
          </div>
          {signal.rating_raw && <div className={styles.figS}>{signal.rating_raw}</div>}
        </div>
        <div className={styles.fig}>
          <div className={styles.figK}>目標價</div>
          <div className={styles.figV}>{fmtTargetPrice(signal.target_price)}</div>
          {sub && <div className={styles.figS}>{sub}</div>}
        </div>
      </div>

      {thesis.length > 0 && (
        <div className={styles.thesis}>
          {thesis.map(t => {
            const label = stanceDisplay(t.key, t.stance)
            const tone = stanceTone(t.key, t.stance)
            return (
              <div key={t.key} className={styles.th}>
                <span className={styles.thK}>{THESIS_LABEL[t.key]}</span>
                <span className={styles.thB}>
                  {label && <span className={`${styles.thS} ${styles[TONE_CLASS[tone]]}`}>{label}</span>}
                  {t.summary && <span className={styles.thX}>{t.summary}</span>}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
