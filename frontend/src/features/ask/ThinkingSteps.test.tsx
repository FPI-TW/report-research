import { act, render, screen, fireEvent } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { ThinkingSteps } from './ThinkingSteps'
import type { Turn } from '../../lib/askReducer'

function turn(over: Partial<Turn>): Turn {
  return {
    id: 't', question: 'Q', phase: 'thinking', stages: ['understanding', 'retrieved'], webUsed: false,
    retrievedCount: null, answer: '', thinkingMs: null, startedAt: 0, sources: [], extSources: [],
    qaId: null, isOfftopic: false, noticeText: null, noticeKind: null,
    feedback: null,
    errorText: null, followups: [], priorVersions: [], versionIndex: 0, rootQaId: null, versionCount: 1, queuePosition: null,
    ...over,
  }
}

afterEach(() => { vi.useRealTimers() })

const steps = () => screen.queryByTestId('thinking-steps')

test('思考中：標頭是目前步驟的流光文字＋計時，步驟清單展開', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking' })} referenced={0} />)
  // stages 到 retrieved：目前進行中的是「檢索研報」
  expect(screen.getByText('檢索研報…')).toBeInTheDocument()
  expect(screen.getByText(/^0\.\d 秒$/)).toBeInTheDocument()
  expect(screen.getByRole('button', { expanded: true })).toBeInTheDocument()
  expect(screen.getByText('理解問題')).toBeInTheDocument()
})

test('思考中還沒抵達任何可見步驟時退回「思考中…」', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking', stages: [] })} referenced={0} />)
  expect(screen.getByText('思考中…')).toBeInTheDocument()
})

test('完成：不放圖示，只有「已思考 N 秒」與「參考 N 篇研報」；歷史紀錄預設收合，點擊展開', () => {
  render(<ThinkingSteps turn={turn({ phase: 'done', thinkingMs: 4200 })} referenced={3} />)
  const head = screen.getByRole('button', { name: /已思考 4 秒/ })
  expect(head).toHaveTextContent('參考 3 篇研報')
  // 標頭裡只剩收合箭頭這一個圖示
  expect(head.querySelectorAll('svg')).toHaveLength(1)
  expect(head.querySelector('svg')!.getAttribute('class')).toMatch(/chevron/)
  expect(head).toHaveAttribute('aria-expanded', 'false')
  expect(steps()).toHaveAttribute('inert')
  fireEvent.click(head)
  expect(head).toHaveAttribute('aria-expanded', 'true')
  expect(steps()).not.toHaveAttribute('inert')
})

test('沒有引用研報時不寫「參考 0 篇研報」', () => {
  render(<ThinkingSteps turn={turn({ phase: 'done', thinkingMs: 2000 })} referenced={0} />)
  expect(screen.queryByText(/參考/)).toBeNull()
})

test('不到一秒也寫「已思考 1 秒」，不寫 0 秒', () => {
  render(<ThinkingSteps turn={turn({ phase: 'done', thinkingMs: 300 })} referenced={0} />)
  expect(screen.getByText('已思考 1 秒')).toBeInTheDocument()
})

test('stages 空（歷史重播）只顯標頭、不顯步驟與收合箭頭', () => {
  render(<ThinkingSteps turn={turn({ phase: 'done', stages: [], thinkingMs: 2000 })} referenced={0} />)
  expect(screen.getByText('已思考 2 秒')).toBeInTheDocument()
  expect(screen.queryByRole('button')).toBeNull()
  expect(screen.queryByText('理解問題')).toBeNull()
})

test('完成時沒有進行中或未到的步驟（終局最後一步轉為完成）', () => {
  const { container } = render(
    <ThinkingSteps turn={turn({ phase: 'done', stages: ['understanding', 'retrieved', 'reading', 'generating'], thinkingMs: 4200 })} referenced={1} />
  )
  expect(screen.getByText('生成回答')).toBeInTheDocument()
  expect(container.querySelectorAll('li[data-state="done"]')).toHaveLength(4)
  expect(container.querySelector('li[data-state="active"], li[data-state="pending"]')).toBeNull()
  expect(container.querySelector('[class*="spin"]')).toBeNull()
})

test('連接線只畫在步驟之間，完成的步驟才填色', () => {
  const { container } = render(<ThinkingSteps turn={turn({ phase: 'thinking', stages: ['understanding', 'retrieved'] })} referenced={0} />)
  const items = container.querySelectorAll('li')
  // 最後一步之後沒有線
  expect(items[items.length - 1].querySelector('[class*="line"]')).toBeNull()
  expect(items[0].querySelector('[class*="lineOn"]')).toBeTruthy()   // 理解問題已完成
  expect(items[1].querySelector('[class*="lineOn"]')).toBeNull()     // 檢索研報進行中
})

test('檢索步驟帶出找到的篇數', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking', stages: ['understanding', 'retrieved'], retrievedCount: 8 })} referenced={0} />)
  expect(screen.getByText('找到 8 篇相關研報')).toBeInTheDocument()
})

test('stages 含 evaluating 時顯示評估補查步驟', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking', stages: ['understanding', 'evaluating'] })} referenced={0} />)
  expect(screen.getAllByText(/評估補查/).length).toBeGreaterThan(0)
})

test('stages 不含 evaluating 時不顯示評估補查步驟', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking', stages: ['understanding', 'retrieved'] })} referenced={0} />)
  expect(screen.queryByText(/評估補查/)).toBeNull()
})

test('開始寫答案後標頭改成「正在撰寫回答」，0.7 秒後自動收合步驟', () => {
  vi.useFakeTimers()
  const { rerender } = render(<ThinkingSteps turn={turn({ phase: 'thinking' })} referenced={0} />)
  rerender(<ThinkingSteps turn={turn({ phase: 'streaming', stages: ['understanding', 'retrieved', 'generating'] })} referenced={0} />)
  expect(screen.getByText('正在撰寫回答')).toBeInTheDocument()
  expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'true')
  act(() => { vi.advanceTimersByTime(700) })
  expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'false')
})

test('重新生成（回到思考中）時重新展開', () => {
  const { rerender } = render(<ThinkingSteps turn={turn({ phase: 'done', thinkingMs: 3000 })} referenced={0} />)
  expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'false')
  rerender(<ThinkingSteps turn={turn({ phase: 'thinking', stages: ['understanding'] })} referenced={0} />)
  expect(screen.getByRole('button')).toHaveAttribute('aria-expanded', 'true')
})

test('失敗：「思考已中斷」，只留已完成的步驟（中斷那一步不算完成）', () => {
  const { container } = render(<ThinkingSteps turn={turn({ phase: 'error', stages: ['understanding', 'retrieved'] })} referenced={0} />)
  expect(screen.getByText('思考已中斷')).toBeInTheDocument()
  expect(container.querySelector('[class*="spin"]')).toBeNull()
  expect(screen.getByText('理解問題')).toBeInTheDocument()
  expect(screen.queryByText('檢索研報')).toBeNull()
})

test('stopped 收掉未到的步驟：停止的輪不會再前進，殘留空心圓會讀成「還在等」', () => {
  render(<ThinkingSteps turn={turn({ phase: 'stopped', stages: ['understanding'] })} referenced={0} />)
  expect(screen.queryByText('檢索研報')).toBeNull()
  expect(screen.queryByText('閱讀整理')).toBeNull()
  expect(screen.queryByText('生成回答')).toBeNull()
})

test('stopped 且 thinkingMs 未量到時標籤是「思考已中斷」而不是「已思考 0 秒」', () => {
  render(<ThinkingSteps turn={turn({ phase: 'stopped', stages: ['understanding'], thinkingMs: null })} referenced={0} />)
  expect(screen.getByText('思考已中斷')).toBeInTheDocument()
  expect(screen.queryByText(/已思考/)).toBeNull()
})

test('stopped 但思考已完成（thinkingMs 有值）仍照實報「已思考 N 秒」', () => {
  render(<ThinkingSteps turn={turn({ phase: 'stopped', stages: ['understanding', 'retrieved', 'reading', 'generating'], thinkingMs: 3000 })} referenced={0} />)
  expect(screen.getByText('已思考 3 秒')).toBeInTheDocument()
})

test('排隊中顯示名次，而不是假裝在思考；排隊期間不計時', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking', queuePosition: 3 })} referenced={0} />)
  expect(screen.getByText('排隊中（第 3 位）…')).toBeInTheDocument()
  expect(screen.queryByText(/檢索研報…|思考中…/)).toBeNull()
  expect(screen.queryByText(/^\d+\.\d 秒$/)).toBeNull()
})

test('排在第一位不報名次（「第 1 位」只是雜訊）', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking', queuePosition: 1 })} referenced={0} />)
  expect(screen.getByText('排隊中…')).toBeInTheDocument()
})
