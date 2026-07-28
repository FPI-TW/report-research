import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, test, vi, afterEach } from 'vitest'
import { RerenderControl } from './RerenderControl'
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

test('渲染可選版型', async () => {
  vi.spyOn(askApi, 'getReportTemplates').mockResolvedValue(TEMPLATES)
  render(withClient(<RerenderControl reportId="r1" />))
  expect(await screen.findByRole('button', { name: '經典券商' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '現代單欄' })).toBeInTheDocument()
})

test('點選版型會呼叫 rerender 並帶對 id', async () => {
  vi.spyOn(askApi, 'getReportTemplates').mockResolvedValue(TEMPLATES)
  const spy = vi.spyOn(askApi, 'rerenderReport').mockResolvedValue({
    rendition_id: 'v1', template_id: 'broker-modern',
  })
  render(withClient(<RerenderControl reportId="r-abc" />))
  fireEvent.click(await screen.findByRole('button', { name: '現代單欄' }))
  await waitFor(() => expect(spy).toHaveBeenCalledWith('r-abc', 'broker-modern'))
})

test('成功後給出可行動的提示', async () => {
  vi.spyOn(askApi, 'getReportTemplates').mockResolvedValue(TEMPLATES)
  vi.spyOn(askApi, 'rerenderReport').mockResolvedValue({
    rendition_id: 'v1', template_id: 'broker-modern',
  })
  render(withClient(<RerenderControl reportId="r1" />))
  fireEvent.click(await screen.findByRole('button', { name: '現代單欄' }))
  expect(await screen.findByText(/重新點上方下載/)).toBeInTheDocument()
})

test('失敗時說明舊 PDF 仍可下載（後端會保留上一版）', async () => {
  vi.spyOn(askApi, 'getReportTemplates').mockResolvedValue(TEMPLATES)
  vi.spyOn(askApi, 'rerenderReport').mockRejectedValue(new Error('500'))
  render(withClient(<RerenderControl reportId="r1" />))
  fireEvent.click(await screen.findByRole('button', { name: '現代單欄' }))
  expect(await screen.findByText(/原本的 PDF 仍可下載/)).toBeInTheDocument()
})

test('模板清單為空時整區不渲染（fail-open,不擋下載）', async () => {
  vi.spyOn(askApi, 'getReportTemplates').mockResolvedValue([])
  const { container } = render(withClient(<RerenderControl reportId="r1" />))
  await Promise.resolve()
  expect(container.querySelector('[role="group"]')).toBeNull()
})

test('不提供語言選項——換皮只換版型,輸出語言由後端沿用產出當時的值', async () => {
  vi.spyOn(askApi, 'getReportTemplates').mockResolvedValue(TEMPLATES)
  render(withClient(<RerenderControl reportId="r1" />))
  await screen.findByRole('button', { name: '經典券商' })
  expect(screen.queryByText(/English/)).toBeNull()
  expect(screen.queryByRole('radiogroup')).toBeNull()
})
