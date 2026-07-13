import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import { AssistantMessage } from './AssistantMessage'
import type { Turn } from '../../lib/askReducer'

function makeTurn(over: Partial<Turn>): Turn {
  return {
    id: 't', question: 'Q', phase: 'done', stages: [], webUsed: false, retrievedCount: null,
    answer: '答案 [1] 內容', thinkingMs: 3000, startedAt: 0,
    sources: [{ n: 1, report_id: 'r1', file_name: 'f.pdf', market: 'TW', report_date: '2026-06-20', is_latest: false }],
    extSources: [], qaId: 'qa1', isOfftopic: false, noticeText: null, offerReport: false, reportTitle: null,
    feedback: null, report: { status: 'idle', pct: 0, stageText: '', downloadUrl: null, title: null, errorText: null },
    errorText: null, followups: [], priorVersions: [], versionIndex: 0, rootQaId: null, versionCount: 1,
    ...over,
  }
}
const noop = {
  onCite: vi.fn(),
  onOpenSources: vi.fn(),
  onFeedback: vi.fn(),
  onNoticeRetry: vi.fn(),
  onErrorRetry: vi.fn(),
  onRegenerate: vi.fn(),
  onFollowup: vi.fn(),
  onSetVersion: vi.fn(),
}

test('done：答案+單一資料來源 {N}+讚/倒讚', () => {
  const onFeedback = vi.fn(); const onOpenSources = vi.fn(); const onCite = vi.fn()
  render(<AssistantMessage turn={makeTurn({})} {...noop} onCite={onCite} onOpenSources={onOpenSources} onFeedback={onFeedback} />)
  fireEvent.click(screen.getByRole('button', { name: '1' })); expect(onCite).toHaveBeenCalledWith(1)
  fireEvent.click(screen.getByRole('button', { name: '資料來源 1' })); expect(onOpenSources).toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '讚' })); expect(onFeedback).toHaveBeenCalledWith('like')
  expect(screen.queryByRole('button', { name: /外部參考/ })).toBeNull() // 無獨立外部參考鈕
})

test('notice：Callout warning + 換個說法重新提問', () => {
  const onNoticeRetry = vi.fn()
  render(<AssistantMessage turn={makeTurn({ phase: 'notice', isOfftopic: true, noticeText: '無法回答此問題', qaId: null })} {...noop} onNoticeRetry={onNoticeRetry} />)
  expect(screen.getByRole('alert')).toHaveTextContent('無法回答此問題')
  fireEvent.click(screen.getByRole('button', { name: '換個說法重新提問' })); expect(onNoticeRetry).toHaveBeenCalled()
})

test('error：Callout error + 重試', () => {
  const onErrorRetry = vi.fn()
  render(<AssistantMessage turn={makeTurn({ phase: 'error', answer: '', errorText: '查詢逾時或失敗' })} {...noop} onErrorRetry={onErrorRetry} />)
  fireEvent.click(screen.getByRole('button', { name: '重試' })); expect(onErrorRetry).toHaveBeenCalled()
})

test('error 但有部分答案：保留答案本文＋錯誤提示＋重試（不抹除已串出的內容）', () => {
  const onErrorRetry = vi.fn()
  const { container } = render(<AssistantMessage turn={makeTurn({ phase: 'error', answer: '已串出的半句回答', qaId: null, errorText: '查詢逾時或失敗' })} {...noop} onErrorRetry={onErrorRetry} />)
  expect(screen.getByText(/已串出的半句回答/)).toBeInTheDocument() // 部分答案本文保留顯示
  expect(container.querySelector('[data-streaming]')).toBeNull()   // 已中斷、非串流，不顯示游標
  fireEvent.click(screen.getByRole('button', { name: '重試' })); expect(onErrorRetry).toHaveBeenCalled() // 錯誤提示仍在
})

test('串流中：本文帶 data-streaming（行內游標鉤）', () => {
  const { container } = render(
    <AssistantMessage turn={makeTurn({ phase: 'streaming', answer: '生成中的內容', qaId: null })} {...noop} />
  )
  expect(container.querySelector('[data-streaming]')).toBeTruthy()
})

test('done：本文不帶 data-streaming', () => {
  const { container } = render(
    <AssistantMessage turn={makeTurn({})} {...noop} />
  )
  expect(container.querySelector('[data-streaming]')).toBeNull()
})

test('stopped phase shows partial answer, 已停止 marker and regenerate', async () => {
  const onRegenerate = vi.fn()
  const turn = makeTurn({ phase: 'stopped', answer: '部分答案', qaId: 'q1' })
  render(<AssistantMessage turn={turn} {...noop} onRegenerate={onRegenerate} />)
  expect(screen.getByText(/部分答案/)).toBeInTheDocument()
  expect(screen.getByText(/已停止/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /重新生成/ }))
  expect(onRegenerate).toHaveBeenCalledOnce()
})

test('renders followup chips and fires onFollowup', async () => {
  const onFollowup = vi.fn()
  const turn = makeTurn({ phase: 'done', qaId: 'q1', followups: ['追問一'] })
  render(<AssistantMessage turn={turn} {...noop} onFollowup={onFollowup} />)
  await userEvent.click(screen.getByRole('button', { name: '追問一' }))
  expect(onFollowup).toHaveBeenCalledWith('追問一')
})

test('version pager shows when versionCount > 1', () => {
  const turn = makeTurn({ phase: 'done', qaId: 'q2', versionCount: 2, versionIndex: 1 })
  render(<AssistantMessage turn={turn} {...noop} />)
  expect(screen.getByText('2/2')).toBeInTheDocument()
})
