import type { ReactNode } from 'react'
import type { RatingConsensus, RatingNorm, Window } from '../../lib/radarSchemas'
import {
  RATING_BUCKET, RATING_DISPLAY, RATING_SAMPLE_MIN, RATING_SAMPLE_NOTE, WINDOW_LABEL,
} from './radarFormat'
import styles from './ConsensusSnapshot.module.css'

const ORDER: RatingNorm[] = ['buy', 'overweight', 'neutral', 'underweight', 'sell']

/** 語意桶 → 立場詞著色。與 InstrumentCard 的 .bull/.neu/.bear 同一組，同一筆資料不該兩套色。 */
const WORD_CLASS = { bull: 'wBull', neu: 'wNeu', bear: 'wBear' } as const

interface Props {
  rating?: RatingConsensus | null
  window: Window
  /**
   * 掛在同一條窄列右端的操作（資料範圍切換）。刻意由外部傳進來而不是自己開一條
   * `.controls` 列：那條列左側永遠是空的，等於為兩顆按鈕多付一整條列高，
   * 而它要控制的東西就在正下方。
   */
  trailing?: ReactNode
}

/**
 * 評等共識：中位立場 ＋ 五級分佈 ＋ 窗期內的評等升降，壓成「市場共識摘要」卡頂端一條窄列。
 *
 * 從雙欄大版面收成一條的取捨：**資訊一項都沒刪**（中位立場、已評等家數、五級分佈、
 * 上調／下調／維持全在），刪掉的只有「評等動能：淨上調 N 家」那一句——它是同一列
 * 三個數字的減法重述，而版面上每一次重述都在稀釋真正的結論。
 *
 * 中位指針與方向軸也一併拿掉：它們原本要解決的是「分布條的段寬座標系」與「固定五級
 * 量表座標系」不一致的老問題（見 git 歷史），而在這個尺寸下五級明細直接印出數字，
 * 指針就不再是必要的解讀輔助。
 */
export function ConsensusSnapshot({ rating, window, trailing }: Props) {
  // 沒有評等時仍然渲染同一條列：trailing 那組控制項管的是下方兩張點圖，
  // 而點圖在「有券商、但沒人給評等」時照樣有內容。
  if (!rating || rating.total_rated === 0) {
    return (
      <div className={styles.strip}>
        <span className={styles.empty}>此窗期尚無評等分布。</span>
        {trailing ? <span className={styles.trailing}>{trailing}</span> : null}
      </div>
    )
  }

  const counts = new Map(rating.distribution.map(d => [d.rating, d.count]))
  const total = rating.total_rated
  const median = rating.median_rating ?? 'neutral'
  const winLabel = WINDOW_LABEL[window] ?? window

  const levels = ORDER
    .map(r => ({ rating: r, count: counts.get(r) ?? 0 }))
    .filter(level => level.count > 0)
  const distLabel = `評等分布，共 ${total} 家：`
    + levels.map(l => `${RATING_DISPLAY[l.rating]} ${l.count} 家`).join('、')

  return (
    <div className={styles.strip}>
      <span className={styles.kicker}>評等共識</span>
      {/* 視覺上維持「評等共識 加碼」兩段（附圖的形狀），但這個詞的來源是
          `rating.median_rating`——它是中位立場，不是「所有券商都同意加碼」。
          差別對讀屏使用者尤其重要，因為他們沒有旁邊那條分佈條可以對照。 */}
      <span className={`${styles.word} ${styles[WORD_CLASS[RATING_BUCKET[median]]]}`}>
        <span className={styles.srOnly}>中位立場：</span>
        {RATING_DISPLAY[median]}
      </span>

      <span className={styles.rule} aria-hidden="true" />

      <span className={styles.count}>{total} 家已評等</span>
      {/* 分布條帶 role="img" ＋ 完整 aria-label：它是唯一不靠文字傳達分布的元素。
          五級明細只在真的有兩級以上時才印——只有一級時它逐字等於左邊那句「N 家已評等」。 */}
      <span className={styles.dist} role="img" aria-label={distLabel}>
        {levels.map(level => (
          <i
            key={level.rating}
            className={styles[level.rating]}
            style={{ width: `${(level.count / total) * 100}%` }}
          />
        ))}
      </span>
      {/* 明細帶色點：分佈條有五種顏色，沒有這組色塊就沒有任何地方說得出哪一段是哪一級
          ——改版前那份 .legend 正是在做這件事，只是它與這行文字重複了兩次數字。 */}
      {levels.length > 1 ? (
        <span className={styles.breakdown}>
          {levels.map(l => (
            <span key={l.rating}>
              <i className={styles[l.rating]} aria-hidden="true" />
              {RATING_DISPLAY[l.rating]} {l.count}
            </span>
          ))}
        </span>
      ) : null}

      {/* 樣本不足是**顯示層的提醒**，不是後端欄位：門檻與說明都由 RATING_SAMPLE_MIN 推導，
          兩者不會各說各話。刻意用 warn 而非 error——家數少是常態不是故障。
          說明句同時進 title（滑鼠）與 sr-only（鍵盤／讀屏）：只掛 title 的話，
          不用滑鼠的人永遠不知道「不足」的門檻是什麼。 */}
      {total < RATING_SAMPLE_MIN ? (
        <span className={styles.lowSample} title={RATING_SAMPLE_NOTE}>
          樣本不足
          <span className={styles.srOnly}>：{RATING_SAMPLE_NOTE}</span>
        </span>
      ) : null}

      <span className={styles.rule} aria-hidden="true" />

      <span className={styles.movement}>
        {winLabel}：
        <span className={styles.mvItem} data-testid="rating-upgrades">
          <b className={styles.up}>{rating.upgrades}</b> 上調
        </span>
        {/* 分隔點對讀屏只是噪音，數字與詞已經自成一組。 */}
        <span className={styles.midDot} aria-hidden="true">・</span>
        <span className={styles.mvItem} data-testid="rating-downgrades">
          <b className={styles.down}>{rating.downgrades}</b> 下調
        </span>
        <span className={styles.midDot} aria-hidden="true">・</span>
        <span className={styles.mvItem} data-testid="rating-unchanged">
          <b className={styles.flat}>{rating.unchanged}</b> 維持
        </span>
      </span>

      {trailing ? <span className={styles.trailing}>{trailing}</span> : null}
    </div>
  )
}
