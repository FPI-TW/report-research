import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { IngestPanel } from './IngestPanel'
import type { Progress } from './progressSchema'

function mk(over: Partial<Progress> = {}): Progress {
  return {
    ts: '', db: { reports: 0, chunks: 0, markets: [] },
    summary: { done: 0, total: 0, remaining: 0, pct: 0 },
    tagging: null, ingest: null,
    pipelines: { web: true, ingest: false, tag: false, summaries: false },
    orchestrator: null, ...over,
  }
}

test('orchestrator.label 優先', () => {
  render(<IngestPanel progress={mk({ orchestrator: { raw: '', timestamp: null, status: 'running', label: '編排器執行中' } })} rateLine="速率 計算中…" />)
  expect(screen.getByText('編排器執行中')).toBeInTheDocument()
})

test('無 orchestrator 有 ingest → 本輪已導入', () => {
  render(<IngestPanel progress={mk({ ingest: { ingested: 42, chunks: 100, fail: 1 } })} rateLine="" />)
  expect(screen.getByText('本輪已導入 42 篇 · 失敗 1')).toBeInTheDocument()
})

test('皆無 → 目前無執行中的導入', () => {
  render(<IngestPanel progress={mk()} rateLine="" />)
  expect(screen.getByText('目前無執行中的導入')).toBeInTheDocument()
})

test('pipelines.ingest true → 不定量條；false → 靜止條', () => {
  const { container: on } = render(<IngestPanel progress={mk({ pipelines: { web: true, ingest: true, tag: false, summaries: false } })} rateLine="" />)
  expect(on.querySelector('[class*="indetBar"]')).not.toBeNull()
  const { container: off } = render(<IngestPanel progress={mk()} rateLine="" />)
  expect(off.querySelector('[class*="indetBar"]')).toBeNull()
  expect(off.querySelector('[class*="indetIdle"]')).not.toBeNull()
})
