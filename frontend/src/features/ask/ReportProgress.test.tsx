import { render, screen, fireEvent, act } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ReportProgress } from './ReportProgress'
import type { ReportState, ReportSection } from '../../lib/askReducer'

const secs = (...states: ReportSection['state'][]): ReportSection[] =>
  states.map((state, i) => ({ position: i, heading: `第${i}節`, state }))

const rs = (over: Partial<ReportState>): ReportState => ({
  status: 'generating', downloadUrl: null, title: null, errorText: null, reportId: null,
  stage: 'writing', sections: [], startedAt: Date.now(), runId: null, queuePosition: null, ...over,
})

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
afterEach(() => { vi.useRealTimers() })

test('顯示章節清單與進行中的那一節', () => {
  render(<ReportProgress report={rs({ sections: secs('done', 'done', 'pending', 'pending') })} />)
  // 使用者最想知道的是「現在在寫哪一章、還剩幾章」——舊版兩者都沒有
  expect(screen.getByText(/撰寫研報中（3\/4）：第2節/)).toBeInTheDocument()
  expect(screen.getByText('第3節')).toBeInTheDocument()
})

test('章節完成數推進進度條的 aria-valuenow', () => {
  const { rerender } = render(<ReportProgress report={rs({ sections: secs('pending', 'pending', 'pending', 'pending') })} />)
  const before = Number(screen.getByRole('progressbar').getAttribute('aria-valuenow'))
  rerender(<ReportProgress report={rs({ sections: secs('done', 'done', 'pending', 'pending') })} />)
  const after = Number(screen.getByRole('progressbar').getAttribute('aria-valuenow'))
  expect(after).toBeGreaterThan(before)
})

test('被略過的章節標示原因，且照樣算進已處理數', () => {
  render(<ReportProgress report={rs({ sections: secs('done', 'skipped', 'pending') })} />)
  expect(screen.getByText('時間不足，已略過')).toBeInTheDocument()
  // 略過的節永遠不會有 section_draft；不計入就會永遠差一節而看似卡死
  expect(screen.getByText(/（3\/3）/)).toBeInTheDocument()
})

test('沒有章節分母時進度條為不定量（無 aria-valuenow）', () => {
  render(<ReportProgress report={rs({ stage: 'writing', sections: [] })} />)
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow')
})

test('已耗時每秒前進——就算後端整段靜默，畫面仍看得出在動', () => {
  const startedAt = Date.now()
  render(<ReportProgress report={rs({ startedAt, sections: secs('pending') })} />)
  expect(screen.getByText('00:00')).toBeInTheDocument()
  act(() => { vi.advanceTimersByTime(3000) })
  expect(screen.getByText('00:03')).toBeInTheDocument()
})

test('提示可離開，並在有 runId 時提供取消', () => {
  const onCancel = vi.fn()
  render(<ReportProgress report={rs({ runId: 'run-1' })} onCancel={onCancel} />)
  expect(screen.getByText(/可以離開此頁/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '取消生成' }))
  expect(onCancel).toHaveBeenCalledWith('run-1')
})

test('沒有 runId（尚未收到 run 事件）時不給取消鈕', () => {
  render(<ReportProgress report={rs({ runId: null })} onCancel={() => {}} />)
  expect(screen.queryByRole('button', { name: '取消生成' })).toBeNull()
})

test('非撰寫階段顯示各自的文案', () => {
  const { rerender } = render(<ReportProgress report={rs({ stage: 'outlining' })} />)
  expect(screen.getByText('規劃章節結構中')).toBeInTheDocument()
  rerender(<ReportProgress report={rs({ stage: 'verifying' })} />)
  expect(screen.getByText('查核引用與數據中')).toBeInTheDocument()
  rerender(<ReportProgress report={rs({ stage: 'rendering' })} />)
  expect(screen.getByText('排版 PDF 中')).toBeInTheDocument()
})

test('排隊中：文案說排隊、進度條轉不定量、不報 0%', () => {
  // 排隊時已耗時/章節/百分比全是零，畫面與「壞掉了」長得一模一樣——文案要先講實話。
  render(<ReportProgress report={rs({ stage: null, sections: [], queuePosition: 2 })} />)
  expect(screen.getByText('排隊等待中（第 2 位）')).toBeInTheDocument()
  expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow')
  expect(screen.queryByText('0%')).toBeNull()
})

test('排隊狀態解除後回到正常階段文案', () => {
  const { rerender } = render(<ReportProgress report={rs({ stage: null, queuePosition: 1 })} />)
  expect(screen.getByText('排隊等待中')).toBeInTheDocument()
  rerender(<ReportProgress report={rs({ stage: 'writing', sections: secs('done', 'pending'), queuePosition: null })} />)
  expect(screen.queryByText(/排隊等待中/)).toBeNull()
  expect(screen.getByText(/撰寫研報中（2\/2）/)).toBeInTheDocument()
})
