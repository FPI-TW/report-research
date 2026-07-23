import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, test, vi, afterEach } from 'vitest'
import { TemplateSelector } from './TemplateSelector'
import * as askApi from '../../lib/askApi'

function withClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>
}

const TEMPLATES = [
  { id: 'ib-classic', name: '經典券商', description: '雙欄', is_default: true, thumbnail: null },
  { id: 'broker-modern', name: '現代單欄', description: '單欄', is_default: false, thumbnail: null },
]

afterEach(() => { vi.restoreAllMocks() })

test('渲染模板卡片並標示預設', async () => {
  vi.spyOn(askApi, 'getReportTemplates').mockResolvedValue(TEMPLATES)
  render(withClient(<TemplateSelector value={undefined} onChange={() => {}} />))
  expect(await screen.findByRole('radio', { name: /經典券商/ })).toBeInTheDocument()
  expect(screen.getByRole('radio', { name: /現代單欄/ })).toBeInTheDocument()
  expect(screen.getByText('預設')).toBeInTheDocument()
})

test('value=undefined 時預設項為 checked', async () => {
  vi.spyOn(askApi, 'getReportTemplates').mockResolvedValue(TEMPLATES)
  render(withClient(<TemplateSelector value={undefined} onChange={() => {}} />))
  const def = await screen.findByRole('radio', { name: /經典券商/ })
  expect(def).toHaveAttribute('aria-checked', 'true')
  expect(screen.getByRole('radio', { name: /現代單欄/ })).toHaveAttribute('aria-checked', 'false')
})

test('點選卡片呼叫 onChange 帶模板 id', async () => {
  vi.spyOn(askApi, 'getReportTemplates').mockResolvedValue(TEMPLATES)
  const onChange = vi.fn()
  render(withClient(<TemplateSelector value={undefined} onChange={onChange} />))
  fireEvent.click(await screen.findByRole('radio', { name: /現代單欄/ }))
  expect(onChange).toHaveBeenCalledWith('broker-modern')
})

test('明確選定時該卡片為 checked', async () => {
  vi.spyOn(askApi, 'getReportTemplates').mockResolvedValue(TEMPLATES)
  render(withClient(<TemplateSelector value="broker-modern" onChange={() => {}} />))
  const sel = await screen.findByRole('radio', { name: /現代單欄/ })
  expect(sel).toHaveAttribute('aria-checked', 'true')
  expect(screen.getByRole('radio', { name: /經典券商/ })).toHaveAttribute('aria-checked', 'false')
})

test('清單為空時整區不渲染（fail-open）', async () => {
  vi.spyOn(askApi, 'getReportTemplates').mockResolvedValue([])
  const { container } = render(withClient(<TemplateSelector value={undefined} onChange={() => {}} />))
  // 空清單 → null；等一個 microtask 讓 query settle
  await Promise.resolve()
  expect(container.querySelector('[role="radiogroup"]')).toBeNull()
})
