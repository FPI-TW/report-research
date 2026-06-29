import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
import * as api from '../../lib/api'
import { theme } from '../../theme'
import MonitorPage from './MonitorPage'

afterEach(() => vi.restoreAllMocks())

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider theme={theme}>
      <QueryClientProvider client={qc}>
        <MonitorPage />
      </QueryClientProvider>
    </MantineProvider>,
  )
}

test('渲染進度數據與 LIVE 標記', async () => {
  vi.spyOn(api, 'getProgress').mockResolvedValue({
    ts: '14:00:00',
    db: { reports: 11401, chunks: 360000, markets: [{ market: 'TW', count: 9000 }] },
    summary: { done: 100, total: 200, remaining: 100, pct: 50 },
    tagging: { pct: 99, done: 11000, total: 11100, fail: 100 },
    ingest: { ingested: 3, fail: 0 },
    pipelines: { web: true, ingest: false, tag: false, summaries: true },
    orchestrator: undefined,
  })
  renderPage()
  expect(await screen.findByText('研報導入監控')).toBeInTheDocument()
  expect(await screen.findByText('LIVE')).toBeInTheDocument()
  expect(await screen.findByText(/標註 TAGGING/)).toBeInTheDocument()
  expect(await screen.findByText('TW')).toBeInTheDocument()
})
