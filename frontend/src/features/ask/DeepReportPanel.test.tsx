import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { DeepReportPanel } from './DeepReportPanel'
import type { ReportState } from '../../lib/askReducer'

const rs = (over: Partial<ReportState>): ReportState => ({ status: 'idle', pct: 0, stageText: '', downloadUrl: null, title: null, errorText: null, ...over })

test('idle 不渲染', () => {
  const { container } = render(<DeepReportPanel report={rs({})} onGenerate={() => {}} onDecline={() => {}} />)
  expect(container.firstChild).toBeNull()
})

test('offered：生成研報/暫時不用 觸發 callback', () => {
  const onGenerate = vi.fn(); const onDecline = vi.fn()
  render(<DeepReportPanel report={rs({ status: 'offered' })} onGenerate={onGenerate} onDecline={onDecline} />)
  expect(screen.getByText('要不要整理成完整 PDF 深度研報？')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '生成研報' })); expect(onGenerate).toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '暫時不用' })); expect(onDecline).toHaveBeenCalled()
})

test('generating 顯示進度與階段文字', () => {
  render(<DeepReportPanel report={rs({ status: 'generating', pct: 50, stageText: '撰寫研報中…' })} onGenerate={() => {}} onDecline={() => {}} />)
  expect(screen.getByText('深度研報生成中…')).toBeInTheDocument()
  expect(screen.getByText('撰寫研報中…')).toBeInTheDocument()
})

test('done 顯示下載連結（scheme 守門相對路徑）', () => {
  render(<DeepReportPanel report={rs({ status: 'done', pct: 100, downloadUrl: '/api/report-doc/rp1/pdf', title: 'T' })} onGenerate={() => {}} onDecline={() => {}} />)
  const dl = screen.getByRole('link', { name: '下載 PDF' })
  expect(dl).toHaveAttribute('href', '/api/report-doc/rp1/pdf')
})

test('error 顯示 Callout 與重試', () => {
  const onGenerate = vi.fn()
  render(<DeepReportPanel report={rs({ status: 'error', errorText: '找不到足夠資料生成研報' })} onGenerate={onGenerate} onDecline={() => {}} />)
  expect(screen.getByRole('alert')).toHaveTextContent('找不到足夠資料生成研報')
  fireEvent.click(screen.getByRole('button', { name: '重試' })); expect(onGenerate).toHaveBeenCalled()
})

test('done：協定相對/跨源 downloadUrl 不渲染下載連結（scheme 守門）', () => {
  const { rerender } = render(<DeepReportPanel report={rs({ status: 'done', pct: 100, downloadUrl: '//evil.com/x', title: 'T' })} onGenerate={() => {}} onDecline={() => {}} />)
  expect(screen.queryByRole('link', { name: '下載 PDF' })).toBeNull()
  rerender(<DeepReportPanel report={rs({ status: 'done', pct: 100, downloadUrl: 'https://evil.com/x', title: 'T' })} onGenerate={() => {}} onDecline={() => {}} />)
  expect(screen.queryByRole('link', { name: '下載 PDF' })).toBeNull()
})
