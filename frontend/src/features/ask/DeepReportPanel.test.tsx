import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, test, vi } from 'vitest'
import { DeepReportPanel } from './DeepReportPanel'
import type { ReportState } from '../../lib/askReducer'
import * as askApi from '../../lib/askApi'

const rs = (over: Partial<ReportState>): ReportState => ({ status: 'idle', pct: 0, stageText: '', downloadUrl: null, title: null, errorText: null, ...over })

// DeepReportPanel 的 offered 狀態嵌 TemplateSelector（useQuery）→ 需 QueryClientProvider。
function withClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>
}

test('idle 不渲染', () => {
  const { container } = render(<DeepReportPanel report={rs({})} onGenerate={() => {}} onDecline={() => {}} />)
  expect(container.firstChild).toBeNull()
})

test('offered：生成研報/暫時不用 觸發 callback', () => {
  const onGenerate = vi.fn(); const onDecline = vi.fn()
  // 未 mock 模板 API → useQuery 失敗（retry:false）→ TemplateSelector 回 null（fail-open）。
  render(withClient(<DeepReportPanel report={rs({ status: 'offered' })} onGenerate={onGenerate} onDecline={onDecline} />))
  expect(screen.getByText('要不要整理成完整 PDF 深度研報？')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '生成研報' })); expect(onGenerate).toHaveBeenCalledWith(undefined)
  fireEvent.click(screen.getByRole('button', { name: '暫時不用' })); expect(onDecline).toHaveBeenCalled()
})

test('offered：選版型後 生成研報 帶著 template_id', async () => {
  vi.spyOn(askApi, 'getReportTemplates').mockResolvedValue([
    { id: 'ib-classic', name: '經典券商', description: '雙欄', is_default: true, thumbnail: null },
    { id: 'broker-modern', name: '現代單欄', description: '單欄', is_default: false, thumbnail: null },
  ])
  const onGenerate = vi.fn()
  render(withClient(<DeepReportPanel report={rs({ status: 'offered' })} onGenerate={onGenerate} onDecline={() => {}} />))
  // 等模板卡片渲染後點選非預設版型
  const card = await screen.findByRole('radio', { name: /現代單欄/ })
  fireEvent.click(card)
  fireEvent.click(screen.getByRole('button', { name: '生成研報' }))
  expect(onGenerate).toHaveBeenCalledWith('broker-modern')
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
