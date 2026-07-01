import { describe, expect, test, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../../theme'
import { ReportPanel } from './ReportPanel'
import { emptyTurn } from '../lib/conversation'
import type { TurnState } from '../lib/conversation'
import type { ReportState } from '../hooks/useReportStream'

const IDLE_REPORT: ReportState = {
  turnId: null,
  phase: 'idle',
  stage: null,
  markdown: '',
  done: null,
  error: null,
}

function wrap(
  turn: TurnState,
  report: ReportState,
  overrides: Partial<{ onStart: () => void; onDismiss: () => void; onOpenFull: (id: string) => void }> = {},
) {
  const onStart = overrides.onStart ?? vi.fn()
  const onDismiss = overrides.onDismiss ?? vi.fn()
  const onOpenFull = overrides.onOpenFull ?? vi.fn()
  const utils = render(
    <MantineProvider theme={theme}>
      <ReportPanel turn={turn} report={report} onStart={onStart} onDismiss={onDismiss} onOpenFull={onOpenFull} />
    </MantineProvider>,
  )
  return { ...utils, onStart, onDismiss, onOpenFull }
}

describe('ReportPanel', () => {
  test('offerReport + idle report → 顯示 report-offer；點「要」呼叫 onStart', () => {
    const turn = { ...emptyTurn('t1', '台積電?'), offerReport: true }
    const { onStart } = wrap(turn, IDLE_REPORT)

    expect(screen.getByTestId('report-offer')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('report-offer-yes'))
    expect(onStart).toHaveBeenCalledTimes(1)
  })

  test('點「不用」→ 隱藏 offer 且呼叫 onDismiss', () => {
    const turn = { ...emptyTurn('t1', '台積電?'), offerReport: true }
    const { onDismiss } = wrap(turn, IDLE_REPORT)

    fireEvent.click(screen.getByTestId('report-offer-no'))
    expect(onDismiss).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('report-offer')).toBeNull()
  })

  test('report.turnId===turn.id 且 phase generating → report-generating + report-preview + report-status 顯示中文階段', () => {
    const turn = emptyTurn('t1', '台積電?')
    const report: ReportState = {
      turnId: 't1',
      phase: 'generating',
      stage: 'writing',
      markdown: '## 摘要\n\n內容一。',
      done: null,
      error: null,
    }
    wrap(turn, report)

    expect(screen.getByTestId('report-generating')).toBeInTheDocument()
    expect(screen.getByTestId('report-preview')).toBeInTheDocument()
    expect(screen.getByTestId('report-status')).toHaveTextContent('撰寫研報中…')
  })

  test('phase done → report-done 顯示下載連結與查看全文；點查看全文呼叫 onOpenFull(report_id)', () => {
    const turn = emptyTurn('t1', '台積電?')
    const report: ReportState = {
      turnId: 't1',
      phase: 'done',
      stage: null,
      markdown: '',
      done: { report_id: 'r1', title: '台積電深度研報', download_url: '/api/report-doc/r1/pdf' },
      error: null,
    }
    const { onOpenFull } = wrap(turn, report)

    const card = screen.getByTestId('report-done')
    expect(card).toHaveTextContent('台積電深度研報')
    const link = screen.getByTestId('report-download')
    expect(link).toHaveAttribute('href', '/api/report-doc/r1/pdf')
    expect(link).toHaveAttribute('download')

    fireEvent.click(screen.getByTestId('report-viewfull'))
    expect(onOpenFull).toHaveBeenCalledWith('r1')
  })

  test('phase error → report-failed + report-retry；點重試呼叫 onStart', () => {
    const turn = emptyTurn('t1', '台積電?')
    const report: ReportState = {
      turnId: 't1',
      phase: 'error',
      stage: null,
      markdown: '',
      done: null,
      error: '研報生成失敗：逾時',
    }
    const { onStart } = wrap(turn, report)

    expect(screen.getByTestId('report-failed')).toHaveTextContent('研報生成失敗：逾時')
    fireEvent.click(screen.getByTestId('report-retry'))
    expect(onStart).toHaveBeenCalledTimes(1)
  })

  test('turn.reports 有歷史資料 → 渲染 report-done 卡（下載+查看全文），優先於其他狀態', () => {
    const turn: TurnState = {
      ...emptyTurn('t1', '台積電?'),
      offerReport: true, // 不應影響：歷史重播優先
      reports: [{ report_id: 'r9', title: '歷史研報', download_url: '/api/report-doc/r9/pdf', created_at: null }],
    }
    const { onOpenFull } = wrap(turn, IDLE_REPORT)

    expect(screen.queryByTestId('report-offer')).toBeNull()
    const card = screen.getByTestId('report-done')
    expect(card).toHaveTextContent('歷史研報')
    expect(screen.getByTestId('report-download')).toHaveAttribute('href', '/api/report-doc/r9/pdf')

    fireEvent.click(screen.getByTestId('report-viewfull'))
    expect(onOpenFull).toHaveBeenCalledWith('r9')
  })

  test('phase done 且 download_url 為安全同源相對路徑 → report-download 有正常可下載 href', () => {
    const turn = emptyTurn('t1', '台積電?')
    const report: ReportState = {
      turnId: 't1',
      phase: 'done',
      stage: null,
      markdown: '',
      done: { report_id: 'r1', title: '台積電深度研報', download_url: '/api/report-doc/r1/pdf' },
      error: null,
    }
    wrap(turn, report)

    const link = screen.getByTestId('report-download')
    expect(link).toHaveAttribute('href', '/api/report-doc/r1/pdf')
    expect(link).toHaveAttribute('download')
  })

  test('phase done 且 download_url 為危險 scheme（javascript:）→ 不渲染危險 href（改為停用控制項）', () => {
    const turn = emptyTurn('t1', '台積電?')
    const report: ReportState = {
      turnId: 't1',
      phase: 'done',
      stage: null,
      markdown: '',
      done: { report_id: 'r1', title: '台積電深度研報', download_url: 'javascript:alert(1)' },
      error: null,
    }
    wrap(turn, report)

    const control = screen.getByTestId('report-download')
    expect(control).not.toHaveAttribute('href')
    expect(control).toBeDisabled()
  })

  test('turn.reports 歷史資料含危險 download_url → 歷史重播卡片（共用 ReportDoneCard）同樣不渲染危險 href', () => {
    const turn: TurnState = {
      ...emptyTurn('t1', '台積電?'),
      reports: [{ report_id: 'r9', title: '歷史研報', download_url: 'javascript:alert(1)', created_at: null }],
    }
    wrap(turn, IDLE_REPORT)

    const control = screen.getByTestId('report-download')
    expect(control).not.toHaveAttribute('href')
    expect(control).toBeDisabled()
  })

  test('report.turnId !== turn.id → 不渲染 generating/done（非本輪），offer/history 才適用', () => {
    const turn = { ...emptyTurn('t1', '台積電?'), offerReport: true }
    const report: ReportState = {
      turnId: 'other-turn',
      phase: 'generating',
      stage: 'writing',
      markdown: '不應顯示',
      done: null,
      error: null,
    }
    wrap(turn, report)

    expect(screen.queryByTestId('report-generating')).toBeNull()
    expect(screen.queryByTestId('report-done')).toBeNull()
    // 非本輪進行中 → 落回 offer 分支
    expect(screen.getByTestId('report-offer')).toBeInTheDocument()
  })

  test('無 offerReport、無 report、無 reports → 回傳 null', () => {
    const turn = emptyTurn('t1', '台積電?')
    const { container } = wrap(turn, IDLE_REPORT)
    // MantineProvider 會於容器內注入 <style>，故不檢查整體空 DOM，只確認無任何實際內容元素渲染
    expect(container.querySelectorAll(':scope > *:not(style)')).toHaveLength(0)
  })
})
