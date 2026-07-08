import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test } from 'vitest'
import { ThinkingSteps } from './ThinkingSteps'
import type { Turn } from '../../lib/askReducer'

function turn(over: Partial<Turn>): Turn {
  return {
    id: 't', question: 'Q', phase: 'thinking', stages: ['understanding', 'retrieved'], webUsed: false,
    retrievedCount: null, answer: '', thinkingMs: null, startedAt: 0, sources: [], extSources: [],
    qaId: null, isOfftopic: false, noticeText: null, offerReport: false, reportTitle: null,
    feedback: null, report: { status: 'idle', pct: 0, stageText: '', downloadUrl: null, title: null, errorText: null },
    errorText: null, ...over,
  }
}

test('thinking 顯示「思考中…」與步驟', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking' })} />)
  expect(screen.getByText('思考中…')).toBeInTheDocument()
  expect(screen.getByText('理解問題')).toBeInTheDocument()
})

test('done 顯示已思考 N 秒、點擊可收合步驟', () => {
  render(<ThinkingSteps turn={turn({ phase: 'done', thinkingMs: 4200 })} />)
  const head = screen.getByRole('button', { name: /已思考 4 秒/ })
  expect(screen.getByText('理解問題')).toBeInTheDocument() // 預設 done 收合？此處展開檢查存在後收合
  fireEvent.click(head)
  expect(screen.queryByText('理解問題')).toBeNull()
})

test('stages 空（歷史重播）只顯 header、不顯步驟與收合箭頭', () => {
  render(<ThinkingSteps turn={turn({ phase: 'done', stages: [], thinkingMs: 2000 })} />)
  expect(screen.getByText('已思考 2 秒')).toBeInTheDocument()
  expect(screen.queryByText('理解問題')).toBeNull()
  expect(screen.queryByText('生成回答')).toBeNull()
})

test('done 且有步驟時不顯示旋轉中的步驟（終止態最後一步應轉為 done）', () => {
  const { container } = render(
    <ThinkingSteps turn={turn({ phase: 'done', stages: ['understanding', 'retrieved', 'reading', 'generating'], thinkingMs: 4200 })} />
  )
  expect(screen.getByText('生成回答')).toBeInTheDocument()
  expect(container.querySelector('[class*="spin"]')).toBeNull()
})
