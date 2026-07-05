import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PipelineStatus } from './PipelineStatus'

test('4 列名稱 + 狀態徽章對應布林', () => {
  render(<PipelineStatus pipelines={{ web: true, ingest: false, tag: true, summaries: false }} />)
  for (const n of ['Web 服務', '報告導入', '語意標註', '摘要生成']) {
    expect(screen.getByText(n)).toBeInTheDocument()
  }
  expect(screen.getAllByText('執行中')).toHaveLength(2)   // web + tag
  expect(screen.getAllByText('已停止')).toHaveLength(2)   // ingest + summaries
})

test('列順序固定為 web/ingest/tag/summaries', () => {
  const { container } = render(<PipelineStatus pipelines={{ web: true, ingest: true, tag: true, summaries: true }} />)
  const names = [...container.querySelectorAll('[class*="pipeName"]')].map(el => el.textContent)
  expect(names).toEqual(['Web 服務', '報告導入', '語意標註', '摘要生成'])
})
