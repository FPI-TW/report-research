import type { ChangeItem, Direction, SnapshotDiff } from '../../lib/radarSchemas'
import { fmtSignedPct } from './radarFormat'

export type Tone = 'up' | 'down' | 'flat'

export function toneOf(direction: Direction): Tone {
  return direction === 'up' ? 'up' : direction === 'down' ? 'down' : 'flat'
}

/** 「有方向」的變動：可比較且上或下。與後端 `scale.is_material` 同一條判準。 */
export function isMaterial(change: ChangeItem): boolean {
  return change.comparable && (change.direction === 'up' || change.direction === 'down')
}

const FIELD_ORDER: Record<ChangeItem['field'], number> = {
  rating: 0, target_price: 1, eps: 2, thesis: 3,
}

function verb(change: ChangeItem): string {
  if (change.field === 'thesis') return change.direction === 'up' ? '轉強' : '轉弱'
  return change.direction === 'up' ? '上修' : '下修'
}

export interface ChangeChip {
  label: string
  value: string
  tone: Tone
}

/**
 * 近期變化事件卡上的變動膠囊：欄位名＋值，例如評等「加碼 → 買進」、目標價「+8.0%」、展望「轉強」。
 * 後端的 `label` 對評等是「前 → 後」，對 EPS 是「2027 FY EPS」這種群組鍵，所以各欄位分開組字。
 */
export function changeChip(change: ChangeItem): ChangeChip {
  const tone = change.comparable ? toneOf(change.direction) : 'flat'
  if (!change.comparable || change.direction === 'incomparable') {
    const label = change.field === 'rating' ? '評等'
      : change.field === 'target_price' ? '目標價'
        : change.field === 'eps' ? 'EPS' : change.label
    return { label, value: '不可比較', tone: 'flat' }
  }
  switch (change.field) {
    case 'rating':
      return { label: '評等', value: change.label, tone }
    case 'target_price':
      return { label: '目標價', value: fmtSignedPct(change.pct_change) || verb(change), tone }
    case 'eps':
      return { label: 'EPS', value: fmtSignedPct(change.pct_change) || verb(change), tone }
    default:
      return { label: change.label, value: verb(change), tone }
  }
}

function phrase(change: ChangeItem): string {
  const pct = change.pct_change != null ? ` ${fmtSignedPct(change.pct_change)}` : ''
  switch (change.field) {
    case 'rating': return `評等 ${change.label}`
    case 'target_price': return `目標價${verb(change)}${pct}`
    case 'eps': return `EPS ${verb(change)}${pct}`
    default: return `${change.label}${verb(change)}`
  }
}

/**
 * 券商歷程每一列的備註：這份報告比前一份改了什麼（最多兩項），沒改就是「維持觀點」。
 * 沒有更早的報告是「首次納入」；有但沒有可比較的欄位就照實說。
 */
export function historyNote(diff: SnapshotDiff | undefined): string {
  if (!diff) return ''
  const material = diff.changes
    .filter(isMaterial)
    .sort((a, b) => FIELD_ORDER[a.field] - FIELD_ORDER[b.field])
  if (material.length) return material.slice(0, 2).map(phrase).join('，')
  const hasPrior = diff.has_prior_report ?? (diff.has_prior_comparable || diff.note !== '沒有更早研報')
  if (!hasPrior) return '首次納入'
  if (!diff.has_prior_comparable) return '無可比較的前次資料'
  return '維持觀點'
}
