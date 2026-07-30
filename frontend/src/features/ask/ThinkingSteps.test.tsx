import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test } from 'vitest'
import { ThinkingSteps } from './ThinkingSteps'
import type { Turn } from '../../lib/askReducer'

function turn(over: Partial<Turn>): Turn {
  return {
    id: 't', question: 'Q', phase: 'thinking', stages: ['understanding', 'retrieved'], webUsed: false,
    retrievedCount: null, answer: '', thinkingMs: null, startedAt: 0, sources: [], extSources: [],
    qaId: null, isOfftopic: false, noticeText: null, noticeKind: null, offerReport: false, reportTitle: null,
    feedback: null, report: { status: 'idle', downloadUrl: null, title: null, errorText: null, reportId: null, stage: null, sections: [], startedAt: null, runId: null, queuePosition: null },
    errorText: null, followups: [], priorVersions: [], versionIndex: 0, rootQaId: null, versionCount: 1, queuePosition: null,
    ...over,
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

test('stages 含 evaluating 時顯示評估補查步驟', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking', stages: ['understanding', 'evaluating'] })} />)
  expect(screen.getByText('評估補查')).toBeInTheDocument()
})

test('stages 不含 evaluating 時不顯示評估補查步驟', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking', stages: ['understanding', 'retrieved'] })} />)
  expect(screen.queryByText('評估補查')).toBeNull()
})

test('排隊中顯示「排隊中…」與原因，而不是假裝在思考', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking', queuePosition: 3 })} />)
  expect(screen.getByText('排隊中（第 3 位）…')).toBeInTheDocument()
  expect(screen.getByText(/伺服器同時處理量已滿/)).toBeInTheDocument()
  expect(screen.queryByText('思考中…')).toBeNull()
})

test('排在第一位不報名次（「第 1 位」只是雜訊）', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking', queuePosition: 1 })} />)
  expect(screen.getByText('排隊中…')).toBeInTheDocument()
})
