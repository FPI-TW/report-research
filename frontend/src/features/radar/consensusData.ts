import type {
  BrokerSummary, RatingBucketCount, RatingNorm, TargetConsensus, TargetGroup,
} from '../../lib/radarSchemas'
import { brokerKey, brokerName } from './brokerKeys'
import { RATING_DISPLAY } from './radarFormat'

/** 五級評等由偏多到偏空；分布條、圖例與文字共用這個順序。 */
export const RATING_ORDER = ['buy', 'overweight', 'neutral', 'underweight', 'sell'] as const satisfies readonly RatingNorm[]

export interface RatingPart {
  rating: (typeof RATING_ORDER)[number]
  label: string
  count: number
}

/** 五級分布 → 有家數的那幾級（照偏多到偏空排）。 */
export function ratingParts(distribution: readonly RatingBucketCount[]): RatingPart[] {
  return RATING_ORDER
    .map(rating => ({
      rating,
      label: RATING_DISPLAY[rating],
      count: distribution.find(d => d.rating === rating)?.count ?? 0,
    }))
    .filter(p => p.count > 0)
}

/** 清單列的分布文字，例如「買進 6、加碼 2、中立 1」但以全形空白（U+3000）分隔，不用「·」。 */
export function ratingPartsText(parts: readonly RatingPart[]): string {
  return parts.map(p => `${p.label} ${p.count}`).join('\u3000')
}

/** 主要幣別那一組目標價：後端已依家數排序，`primary_currency` 就是第一組。 */
export function primaryTargetGroup(target: TargetConsensus | null | undefined): TargetGroup | null {
  if (!target?.groups.length) return null
  return target.groups.find(g => g.currency === target.primary_currency) ?? target.groups[0]
}

/** 點與點水平距離小於這個百分比就往上疊一層（14px 的點在 1000px 寬約 1.4%，留些餘裕）。 */
const OVERLAP_PCT = 2.6

export interface TargetDot {
  key: string
  broker: string
  value: number
  /** 0–100，軸上的位置。 */
  x: number
  /** 第幾層（0 是貼著軸線那一層）。 */
  level: number
}

/**
 * 各券商目標價 → 軸上的點。只收「這段期間內有最新研報（不是期間外）、幣別與共識相同」的券商，
 * 也就是共識中位數算的那一群——點與中位數標線講的才是同一件事。
 *
 * 疊放由小到大逐點放：同一層已有距離太近的點就往上一層，與原型相同。
 */
export function targetDots(brokers: readonly BrokerSummary[], group: TargetGroup): TargetDot[] {
  const values = brokers
    .filter(b => !b.stale && brokerKey(b) && b.latest_target_price != null
      && (b.latest_target_currency?.trim() || null) === group.currency)
    .map(b => ({ key: brokerKey(b), broker: brokerName(b), value: b.latest_target_price as number }))
    .sort((a, b) => a.value - b.value || a.broker.localeCompare(b.broker))
  if (!values.length) return []
  const lo = Math.min(group.low, values[0].value)
  const hi = Math.max(group.high, values[values.length - 1].value)
  const span = hi - lo
  const placed: Array<{ x: number; level: number }> = []
  return values.map(v => {
    const x = span > 0 ? ((v.value - lo) / span) * 100 : 50
    let level = 0
    while (placed.some(p => p.level === level && Math.abs(p.x - x) < OVERLAP_PCT)) level += 1
    placed.push({ x, level })
    return { ...v, x, level }
  })
}
