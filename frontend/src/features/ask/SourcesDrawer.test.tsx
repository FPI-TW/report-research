import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { SourcesDrawer } from './SourcesDrawer'
import type { Turn } from '../../lib/askReducer'

function turn(over: Partial<Turn>): Turn {
  return {
    id: 't', question: 'Q', phase: 'done', stages: [], webUsed: false, retrievedCount: null,
    answer: '', thinkingMs: null, startedAt: 0,
    sources: [{ n: 1, report_id: 'r1', file_name: '台積電.pdf', market: 'TW', report_date: '2026-06-20', is_latest: false }],
    extSources: [{ title: '外部新聞', url: 'https://news.example.com/a' }],
    qaId: 'qa1', isOfftopic: false, noticeText: null, offerReport: false, reportTitle: null,
    feedback: null, report: { status: 'idle', pct: 0, stageText: '', downloadUrl: null, title: null, errorText: null },
    errorText: null, followups: [], priorVersions: [], versionIndex: 0, rootQaId: null, versionCount: 1,
    ...over,
  }
}

test('子標計數為研報+網路總數；點研報卡開報告；ESC 關閉', () => {
  const onClose = vi.fn(); const onOpenReport = vi.fn()
  render(<SourcesDrawer open turn={turn({})} onClose={onClose} onOpenReport={onOpenReport} />)
  expect(screen.getByText('資料來源 · 2')).toBeInTheDocument()
  fireEvent.click(screen.getByText('台積電.pdf'))
  expect(onOpenReport).toHaveBeenCalledWith('r1', '台積電.pdf')
  const ext = screen.getByRole('link', { name: /外部新聞/ })
  expect(ext).toHaveAttribute('target', '_blank')
  expect(ext).toHaveAttribute('rel', 'noopener noreferrer')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(onClose).toHaveBeenCalled()
})

test('closed 或 null 不渲染', () => {
  const { container } = render(<SourcesDrawer open={false} turn={turn({})} onClose={() => {}} onOpenReport={() => {}} />)
  expect(container.firstChild).toBeNull()
})
