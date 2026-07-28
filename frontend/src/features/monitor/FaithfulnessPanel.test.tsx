import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { FaithfulnessPanel } from './FaithfulnessPanel'
import type { Evaluation } from './progressSchema'

const base: Evaluation = {
  qa: { total: 40, checked: 3, degraded: 1, below_min: 1, avg_score: 0.5634, latest: '2026-07-28' },
  report: { total: 6, checked: 1, degraded: 0, below_min: 1, avg_score: 0.4118, latest: '2026-07-28' },
  min_score: 0.9,
}

test('兩個來源分開呈現，數字不混算', () => {
  render(<FaithfulnessPanel evaluation={base} />)
  expect(screen.getByText('問答')).toBeInTheDocument()
  expect(screen.getByText('研報')).toBeInTheDocument()
  expect(screen.getByText('已查核 3/40')).toBeInTheDocument()
  expect(screen.getByText('已查核 1/6')).toBeInTheDocument()
  expect(screen.getByText('平均 0.563')).toBeInTheDocument()
})

test('degraded 與待複核 > 0 才標警示色', () => {
  const { container } = render(<FaithfulnessPanel evaluation={base} />)
  const warn = [...container.querySelectorAll('[class*="fWarn"]')].map(e => e.textContent)
  // 問答：fail-open 1、待複核 1；研報：fail-open 0（不警示）、待複核 1
  expect(warn).toContain('fail-open 1')
  expect(warn).toContain('待複核 1')
  expect(warn).not.toContain('fail-open 0')
})

test('avg_score 為 null（全部 fail-open）→ 顯 — 而非 0', () => {
  // 0 會被讀成「量到了而且滿分不合格」，比留白危險。
  render(<FaithfulnessPanel evaluation={{
    ...base,
    qa: { total: 5, checked: 2, degraded: 2, below_min: 0, avg_score: null, latest: null },
    report: null,
  }} />)
  expect(screen.getByText('平均 —')).toBeInTheDocument()
  expect(screen.getByText('尚無查核（門檻 0.9）')).toBeInTheDocument()
})

test('來源為 null → 該列不出現，不是印空白列', () => {
  render(<FaithfulnessPanel evaluation={{ ...base, report: null }} />)
  expect(screen.queryByText('研報')).not.toBeInTheDocument()
})

test('後端未提供 evaluation → 降級文案，不整張消失', () => {
  render(<FaithfulnessPanel evaluation={undefined} />)
  expect(screen.getByText('此版後端未提供查核統計')).toBeInTheDocument()
})
