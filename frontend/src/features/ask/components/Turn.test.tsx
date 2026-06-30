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

  test('來源清單可點 → onCite(report_id)（需先展開 toggle）', () => {
    const onCite = vi.fn()
    wrap(doneTurn(), onCite)
    // 預設收合：ask-src 尚未存在
    expect(screen.queryByTestId('ask-src')).toBeNull()
    // 展開來源
    fireEvent.click(screen.getByTestId('ask-sources-toggle'))
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

  test('資料來源切換鈕：預設收合，點擊展開並翻 aria-expanded，再點收合', () => {
    wrap(doneTurn())
    // 預設：列表不存在，toggle 按鈕存在且 aria-expanded=false
    expect(screen.queryByTestId('ask-sources')).toBeNull()
    const toggleBtn = screen.getByTestId('ask-sources-toggle')
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'false')
    // 計數徽章顯示 1
    expect(toggleBtn).toHaveTextContent('1')
    // 點一下：展開
    fireEvent.click(toggleBtn)
    expect(screen.getByTestId('ask-sources')).toBeInTheDocument()
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'true')
    // 再點：收合
    fireEvent.click(toggleBtn)
    expect(screen.queryByTestId('ask-sources')).toBeNull()
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'false')
  })

  test('外部參考切換鈕：有 extSources 時顯示，點擊展開 ask-ext', () => {
    wrap(
      doneTurn({
        extSources: [{ url: 'https://example.com', title: 'Example' }],
      }),
    )
    expect(screen.queryByTestId('ask-ext')).toBeNull()
    const extBtn = screen.getByTestId('ask-ext-toggle')
    expect(extBtn).toHaveAttribute('aria-expanded', 'false')
    expect(extBtn).toHaveTextContent('1')
    fireEvent.click(extBtn)
    expect(screen.getByTestId('ask-ext')).toBeInTheDocument()
    expect(extBtn).toHaveAttribute('aria-expanded', 'true')
  })

  test('重複點同一讚值不重送 sendFeedback（dedup）', () => {
    wrap(doneTurn())
    fireEvent.click(screen.getByRole('button', { name: '讚' }))
    fireEvent.click(screen.getByRole('button', { name: '讚' }))
    expect(api.sendFeedback).toHaveBeenCalledTimes(1)
  })

  test('非 http(s) ext url 不產生 <a> 錨點，僅以文字顯示 title', () => {
    wrap(
      doneTurn({
        extSources: [{ url: 'javascript:alert(1)', title: 'Dangerous' }],
      }),
    )
    // 展開外部參考區塊
    fireEvent.click(screen.getByTestId('ask-ext-toggle'))
    const ext = screen.getByTestId('ask-ext')
    // 不應有指向危險 url 的 <a>
    expect(ext.querySelector('a[href="javascript:alert(1)"]')).toBeNull()
    // 但 title 文字應顯示
    expect(ext).toHaveTextContent('Dangerous')
  })

  test('http(s) ext url 正常渲染為 <a> 錨點', () => {
    wrap(
      doneTurn({
        extSources: [{ url: 'https://example.com', title: 'Safe Link' }],
      }),
    )
    fireEvent.click(screen.getByTestId('ask-ext-toggle'))
    const ext = screen.getByTestId('ask-ext')
    expect(ext.querySelector('a[href="https://example.com"]')).not.toBeNull()
    expect(ext).toHaveTextContent('Safe Link')
  })
})
