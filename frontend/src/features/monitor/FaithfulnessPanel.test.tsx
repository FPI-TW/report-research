import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { FaithfulnessPanel } from './FaithfulnessPanel'
import type { Evaluation } from './progressSchema'

const base: Evaluation = {
  qa: { total: 40, checked: 3, degraded: 1, below_min: 1, avg_score: 0.5634, latest: '2026-07-28' },
  min_score: 0.9,
}

test('問答來源的三個訊號都呈現', () => {
  render(<FaithfulnessPanel evaluation={base} />)
  expect(screen.getByText('問答')).toBeInTheDocument()
  expect(screen.getByText('已查核 3/40')).toBeInTheDocument()
  expect(screen.getByText('平均 0.563')).toBeInTheDocument()
  expect(screen.getByText('最後查核 2026-07-28')).toBeInTheDocument()
})

test('degraded 與待複核 > 0 才標警示色', () => {
  const { container } = render(<FaithfulnessPanel evaluation={{
    ...base,
    qa: { total: 40, checked: 3, degraded: 0, below_min: 1, avg_score: 0.5634, latest: '2026-07-28' },
  }} />)
  const warn = [...container.querySelectorAll('[class*="fWarn"]')].map(e => e.textContent)
  expect(warn).toContain('待複核 1')
  expect(warn).not.toContain('fail-open 0')
})

test('avg_score 為 null（全部 fail-open）→ 顯 — 而非 0', () => {
  // 0 會被讀成「量到了而且滿分不合格」，比留白危險。
  render(<FaithfulnessPanel evaluation={{
    ...base,
    qa: { total: 5, checked: 2, degraded: 2, below_min: 0, avg_score: null, latest: null },
  }} />)
  expect(screen.getByText('平均 —')).toBeInTheDocument()
  expect(screen.getByText('尚無查核（門檻 0.9）')).toBeInTheDocument()
})

test('來源為 null → 該列不出現，不是印空白列', () => {
  render(<FaithfulnessPanel evaluation={{ ...base, qa: null }} />)
  expect(screen.queryByText('問答')).not.toBeInTheDocument()
})

test('後端未提供 evaluation → 降級文案，不整張消失', () => {
  render(<FaithfulnessPanel evaluation={undefined} />)
  expect(screen.getByText('此版後端未提供查核統計')).toBeInTheDocument()
})

test('主數字計所有判定尺，分數類只計現行判定尺並標出平均樣本數', () => {
  // 換 judge 後：7 筆裡只有 3 筆是新尺量的。主數字維持 7（覆蓋率），不能驟降成 3。
  render(<FaithfulnessPanel evaluation={{
    ...base,
    qa: {
      ...base.qa!, checked: 7, judge_model: 'claude-haiku-4-5', judge_since: '2026-07-02',
      other_judge_checked: 4, judge_checked: 3, avg_n: 2,
    },
  }} />)
  expect(screen.getByText('已查核 7/40')).toBeInTheDocument()
  const scale = screen.getByText(/只計判定尺 claude-haiku-4-5，自 2026-07-02 起/)
  expect(scale.textContent).toContain('fail-open、待複核、平均只計判定尺')
  expect(scale.textContent).toContain('（該尺已查核 3、平均樣本數 2）')
  expect(scale.textContent).toContain('另有 4 筆其他判定尺的結果只計入已查核數')
  expect(scale.textContent).not.toContain('n=')
})

test('沒有其他尺的結果 → 不印「另有 0 筆」', () => {
  render(<FaithfulnessPanel evaluation={{
    ...base,
    qa: {
      ...base.qa!, judge_model: 'claude-haiku-4-5', judge_since: null, other_judge_checked: 0,
      judge_checked: 3, avg_n: 2,
    },
  }} />)
  expect(screen.getByText(/只計判定尺 claude-haiku-4-5（該尺已查核 3、平均樣本數 2）/)).toBeInTheDocument()
  expect(screen.queryByText(/另有/)).not.toBeInTheDocument()
})

test('後端沒有 judge_checked／avg_n → 判定尺那一行不硬湊數字', () => {
  render(<FaithfulnessPanel evaluation={{
    ...base,
    qa: { ...base.qa!, judge_model: 'claude-haiku-4-5', judge_since: null, other_judge_checked: 0 },
  }} />)
  const scale = screen.getByText(/只計判定尺 claude-haiku-4-5/)
  expect(scale.textContent).not.toContain('（')
})

test('切到 DeepSeek judge、窗期內還有舊尺的列 → 標新量尺，日期取自 judge_since', () => {
  render(<FaithfulnessPanel evaluation={{
    ...base,
    qa: {
      ...base.qa!, checked: 9, judge_model: 'deepseek-flash', judge_since: '2026-09-25',
      other_judge_checked: 6, judge_checked: 3, avg_n: 3,
    },
  }} />)
  const scale = screen.getByText(/只計判定尺 deepseek-flash/)
  expect(scale.textContent).toContain('判定尺 deepseek-flash：新量尺（自 2026-09-25 起，DeepSeek）')
  expect(scale.textContent).toContain('另有 6 筆其他判定尺的結果只計入已查核數')
})

test('剛切換、新尺尚無查核 → 新量尺但不編日期', () => {
  render(<FaithfulnessPanel evaluation={{
    ...base,
    qa: {
      ...base.qa!, judge_model: 'deepseek-flash', judge_since: null, other_judge_checked: 3,
      judge_checked: 0, avg_n: 0,
    },
  }} />)
  expect(screen.getByText(/新量尺（尚無查核，DeepSeek）/)).toBeInTheDocument()
})

test('窗期內已全是 DeepSeek 的列 → 不再稱新量尺（judge_since 只是窗期起點）', () => {
  render(<FaithfulnessPanel evaluation={{
    ...base,
    qa: {
      ...base.qa!, judge_model: 'deepseek-flash', judge_since: '2026-10-01', other_judge_checked: 0,
      judge_checked: 3, avg_n: 3,
    },
  }} />)
  const scale = screen.getByText(/只計判定尺 deepseek-flash，自 2026-10-01 起/)
  expect(scale.textContent).not.toContain('新量尺')
})

test('舊後端沒有量尺欄位 → 不印判定尺那一行', () => {
  render(<FaithfulnessPanel evaluation={base} />)
  expect(screen.queryByText(/判定尺/)).not.toBeInTheDocument()
})
