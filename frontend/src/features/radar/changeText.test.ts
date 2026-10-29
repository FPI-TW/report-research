import { describe, expect, it } from 'vitest'
import type { ChangeItem, SnapshotDiff } from '../../lib/radarSchemas'
import { changeChip, historyNote } from './changeText'

function change(over: Partial<ChangeItem>): ChangeItem {
  return {
    field: 'target_price', label: '目標價', direction: 'up',
    pct_change: 8, comparable: true,
    ...over,
  }
}

function diff(over: Partial<SnapshotDiff>): SnapshotDiff {
  return {
    to_report_date: '2026-10-27', changes: [],
    has_prior_report: true, has_prior_comparable: true,
    ...over,
  }
}

describe('changeChip', () => {
  it('評等用後端的「前 → 後」，紅升綠降', () => {
    expect(changeChip(change({ field: 'rating', label: '加碼 → 買進', direction: 'up', pct_change: null })))
      .toEqual({ label: '評等', value: '加碼 → 買進', tone: 'up' })
  })

  it('目標價與 EPS 印帶正負號的幅度', () => {
    expect(changeChip(change({ direction: 'down', pct_change: -6 })))
      .toEqual({ label: '目標價', value: '−6.0%', tone: 'down' })
    expect(changeChip(change({ field: 'eps', label: '2027 FY EPS', pct_change: 2 })))
      .toEqual({ label: 'EPS', value: '+2.0%', tone: 'up' })
  })

  it('觀點變動印維度與轉強／轉弱', () => {
    expect(changeChip(change({ field: 'thesis', label: '展望', direction: 'down', pct_change: null })))
      .toEqual({ label: '展望', value: '轉弱', tone: 'down' })
  })

  it('不可比較是中性色', () => {
    expect(changeChip(change({ comparable: false, direction: 'incomparable' })))
      .toEqual({ label: '目標價', value: '不可比較', tone: 'flat' })
  })
})

describe('historyNote', () => {
  it('列出最多兩項有方向的變動，評等優先', () => {
    expect(historyNote(diff({
      changes: [
        change({ field: 'eps', label: '2027 FY EPS', pct_change: 2 }),
        change({ pct_change: 8 }),
        change({ field: 'rating', label: '加碼 → 買進', pct_change: null }),
      ],
    }))).toBe('評等 加碼 → 買進，目標價上修 +8.0%')
  })

  it('持平與不可比較不算變動', () => {
    expect(historyNote(diff({
      changes: [change({ direction: 'flat', pct_change: 0 }), change({ comparable: false, direction: 'incomparable' })],
    }))).toBe('維持觀點')
  })

  it('沒有更早的報告是首次納入；有但不可比是照實說', () => {
    expect(historyNote(diff({ has_prior_report: false, has_prior_comparable: false }))).toBe('首次納入')
    expect(historyNote(diff({ has_prior_report: true, has_prior_comparable: false }))).toBe('無可比較的前次資料')
  })

  it('舊後端沒有 has_prior_report 時退回 note 判斷', () => {
    expect(historyNote(diff({ has_prior_report: undefined, has_prior_comparable: false, note: '沒有更早研報' })))
      .toBe('首次納入')
  })
})
