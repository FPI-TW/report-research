import { describe, expect, test, vi, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../../theme'
import { Turn } from './Turn'
import { emptyTurn } from '../lib/conversation'
import type { TurnState } from '../lib/conversation'

vi.mock('../api', () => ({ sendFeedback: vi.fn().mockResolvedValue(undefined) }))
import * as api from '../api'

afterEach(() => vi.clearAllMocks())

const wrap = (t: TurnState, onCite = vi.fn()) =>
  render(
    <MantineProvider theme={theme}>
      <Turn turn={t} onCite={onCite} />
    </MantineProvider>,
  )

const doneTurn = (over: Partial<TurnState> = {}): TurnState => ({
  ...emptyTurn('t1', '台積電?'),
  phase: 'done',
  answer: '台積電[1] 表現佳。',
  qaId: 'q1',
  sources: [{ n: 1, report_id: 'r1', file_name: 'a.pdf', market: 'TW', report_date: '2026-01-01', is_latest: true }],
  ...over,
})

describe('Turn', () => {
  test('渲染問題與答案 markdown', () => {
    wrap(doneTurn())
    expect(screen.getByTestId('ask-q')).toHaveTextContent('台積電?')
    expect(screen.getByTestId('ask-answer')).toHaveTextContent('台積電[1] 表現佳。')
  })

  test('來源清單可點 → onCite(report_id)', () => {
    const onCite = vi.fn()
    wrap(doneTurn(), onCite)
    fireEvent.click(screen.getAllByTestId('ask-src')[0])
    expect(onCite).toHaveBeenCalledWith('r1')
  })

  test('引用 chip 點擊 → onCite(本輪來源 report_id)', () => {
    const onCite = vi.fn()
    wrap(doneTurn(), onCite)
    fireEvent.click(screen.getByTestId('ask-answer').querySelector('.cite')!)
    expect(onCite).toHaveBeenCalledWith('r1')
  })

  test('離題卡', () => {
    wrap(doneTurn({ phase: 'notice', notice: '無法回答此問題的內容' }))
    expect(screen.getByTestId('ask-notice')).toHaveTextContent('無法回答此問題的內容')
    expect(screen.queryByTestId('ask-answer')).toBeNull()
  })

  test('讚回饋呼叫 sendFeedback', () => {
    wrap(doneTurn())
    fireEvent.click(screen.getByRole('button', { name: '讚' }))
    expect(api.sendFeedback).toHaveBeenCalledWith('q1', 'like')
  })
})
