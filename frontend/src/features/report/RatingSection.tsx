import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { ArrowDown, ArrowUp, ChevronRight } from 'lucide-react'
import { instrumentHref } from '../radar/catalogState'
import { marketSchema, type Market } from '../../lib/radarSchemas'
import type { ReadingRatingNorm, Signal } from '../../lib/readingSchemas'
import { RATING_DISPLAY, fmtRevisionPct, fmtTargetPrice } from './readingFormat'
import { useConsensusCount, useTargetRevision } from './useReading'
import panel from './panel.module.css'
import styles from './RatingSection.module.css'

/** 評等 → 徽章類名（台股慣例紅漲綠跌，色票在 tokens.css 的 --tf-badge-*）。 */
const BADGE_CLASS: Record<ReadingRatingNorm, string> = {
  buy: styles.rBuy,
  overweight: styles.rOverweight,
  neutral: styles.rNeutral,
  underweight: styles.rUnderweight,
  sell: styles.rSell,
  unknown: styles.rNeutral,
}

/** 訊號的市場不在券商共識的市場清單裡時不給連結（共識頁會 422），卡片照常顯示。 */
function marketOf(signal: Signal): Market | null {
  const m = marketSchema.safeParse(signal.market)
  return m.success ? m.data : null
}

function consensusHref(market: Market, code: string): string {
  return instrumentHref(new URLSearchParams(), market, code)
}

/** 標題列右側的「N 家券商共識 ›」。券商數還沒回來或查不到時只寫「券商共識」。 */
function ConsensusLink({ signal }: { signal: Signal }) {
  const market = marketOf(signal)
  const count = useConsensusCount(signal.instrument_code, market)
  if (!market) return null
  const n = count.data
  return (
    <Link className={styles.lk} to={consensusHref(market, signal.instrument_code)}>
      {n ? <><span className={styles.num}>{n}</span> 家券商共識</> : '券商共識'}
      <ChevronRight size={13} strokeWidth={1.8} aria-hidden />
    </Link>
  )
}

function RatingCard({ signal, reportId }: { signal: Signal; reportId: string }) {
  const market = marketOf(signal)
  const revision = useTargetRevision(signal.instrument_code, market, signal.broker, reportId)
  const rating = signal.rating_normalized
  const name = signal.instrument_name?.trim() || null
  const rev = revision.data ?? null
  const currency = signal.target_currency?.trim()

  const body: ReactNode = (
    <>
      <span className={`${styles.badge} ${BADGE_CLASS[rating]}`}>{RATING_DISPLAY[rating]}</span>
      <span className={styles.mid}>
        <span className={styles.k}>目標價{currency ? `（${currency}）` : ''}</span>
        <span className={styles.vRow}>
          <span className={styles.v}>{fmtTargetPrice(signal.target_price)}</span>
          {rev && (
            <span className={rev.direction === 'up' ? styles.up : styles.down}>
              {rev.direction === 'up'
                ? <ArrowUp size={11} strokeWidth={2.6} aria-hidden />
                : <ArrowDown size={11} strokeWidth={2.6} aria-hidden />}
              {rev.direction === 'up' ? '上調' : '下修'} {fmtRevisionPct(rev.pct)}
            </span>
          )}
        </span>
      </span>
      {/* 一份研報可能同時對多檔標的有訊號（一列＝一份研報 × 一個標的），所以每張卡都
          要標出在講誰。名稱缺值（後端解析不出公司名）是常態，回退成只顯示代號。 */}
      <span className={styles.who}>
        {name && <span className={styles.k}>{name}</span>}
        <span className={styles.code}>{signal.instrument_code}</span>
      </span>
    </>
  )

  if (!market) return <div className={styles.card}>{body}</div>
  return (
    <Link className={`${styles.card} ${styles.cardOn}`} to={consensusHref(market, signal.instrument_code)}>
      {body}
      <span className={styles.srOnly}>：前往券商共識</span>
    </Link>
  )
}

/**
 * 本篇評等。資料來自 ReadingDoc 的訊號；呼叫端負責「無訊號整區不進 DOM」
 * （多數研報沒有訊號，那是常態不是錯誤，版面只變短不跳動）。
 *
 * 收盤價沒有資料來源（本系統不存行情），所以卡片右欄改放標的，不留空欄。
 */
export function RatingSection({ signals, reportId }: { signals: Signal[]; reportId: string }) {
  const single = signals.length === 1 ? signals[0] : null
  return (
    <section className={panel.sec} aria-labelledby="rd-rate">
      <div className={panel.head}>
        <h2 id="rd-rate" className={panel.h}>本篇評等</h2>
        {/* 多檔標的時標題列不知道該連哪一檔，改由各張卡自己連 */}
        {single && <ConsensusLink signal={single} />}
      </div>
      <div className={styles.cards}>
        {signals.map(s => (
          <RatingCard key={`${s.market}-${s.instrument_code}`} signal={s} reportId={reportId} />
        ))}
      </div>
    </section>
  )
}
