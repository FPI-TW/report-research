import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { AssistantMessage } from './AssistantMessage'
import type { Turn } from '../../lib/askReducer'

function turn(over: Partial<Turn>): Turn {
  return {
    id: 't', question: 'Q', phase: 'done', stages: [], webUsed: false, retrievedCount: null,
    answer: '答案 [1] 內容', thinkingMs: 3000, startedAt: 0,
    sources: [{ n: 1, report_id: 'r1', file_name: 'f.pdf', market: 'TW', report_date: '2026-06-20', is_latest: false }],
    extSources: [], qaId: 'qa1', isOfftopic: false, noticeText: null, offerReport: false, reportTitle: null,
    feedback: null, report: { status: 'idle', pct: 0, stageText: '', downloadUrl: null, title: null, errorText: null },
    errorText: null, ...over,
  }
}
const noop = () => {}

test('done：答案+單一資料來源 {N}+讚/倒讚', () => {
  const onFeedback = vi.fn(); const onOpenSources = vi.fn(); const onCite = vi.fn()
  render(<AssistantMessage turn={turn({})} onCite={onCite} onOpenSources={onOpenSources} onFeedback={onFeedback} onNoticeRetry={noop} onErrorRetry={noop} />)
  fireEvent.click(screen.getByRole('button', { name: '1' })); expect(onCite).toHaveBeenCalledWith(1)
  fireEvent.click(screen.getByRole('button', { name: '資料來源 1' })); expect(onOpenSources).toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '讚' })); expect(onFeedback).toHaveBeenCalledWith('like')
  expect(screen.queryByRole('button', { name: /外部參考/ })).toBeNull() // 無獨立外部參考鈕
})

test('notice：Callout warning + 換個說法重新提問', () => {
  const onNoticeRetry = vi.fn()
  render(<AssistantMessage turn={turn({ phase: 'notice', isOfftopic: true, noticeText: '無法回答此問題', qaId: null })} onCite={noop} onOpenSources={noop} onFeedback={noop} onNoticeRetry={onNoticeRetry} onErrorRetry={noop} />)
  expect(screen.getByRole('alert')).toHaveTextContent('無法回答此問題')
  fireEvent.click(screen.getByRole('button', { name: '換個說法重新提問' })); expect(onNoticeRetry).toHaveBeenCalled()
})

test('error：Callout error + 重試', () => {
  const onErrorRetry = vi.fn()
  render(<AssistantMessage turn={turn({ phase: 'error', answer: '', errorText: '查詢逾時或失敗' })} onCite={noop} onOpenSources={noop} onFeedback={noop} onNoticeRetry={noop} onErrorRetry={onErrorRetry} />)
  fireEvent.click(screen.getByRole('button', { name: '重試' })); expect(onErrorRetry).toHaveBeenCalled()
})

test('串流中：本文帶 data-streaming（行內游標鉤）', () => {
  const { container } = render(
    <AssistantMessage turn={turn({ phase: 'streaming', answer: '生成中的內容', qaId: null })}
      onCite={noop} onOpenSources={noop} onFeedback={noop} onNoticeRetry={noop} onErrorRetry={noop} />
  )
  expect(container.querySelector('[data-streaming]')).toBeTruthy()
})

test('done：本文不帶 data-streaming', () => {
  const { container } = render(
    <AssistantMessage turn={turn({})}
      onCite={noop} onOpenSources={noop} onFeedback={noop} onNoticeRetry={noop} onErrorRetry={noop} />
  )
  expect(container.querySelector('[data-streaming]')).toBeNull()
})
