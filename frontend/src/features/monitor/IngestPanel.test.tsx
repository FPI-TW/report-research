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

test('orchestrator 與 ingest 同時存在 → orchestrator.label 優先（?? 短路）', () => {
  render(
    <IngestPanel
      progress={mk({
        orchestrator: { raw: '', timestamp: null, status: 'running', label: '編排器執行中' },
        ingest: { ingested: 42, chunks: 100, fail: 1 },
      })}
      rateLine=""
    />,
  )
  expect(screen.getByText('編排器執行中')).toBeInTheDocument()
  expect(screen.queryByText('本輪已導入 42 篇 · 失敗 1')).toBeNull()
})

test('皆無 → 目前無執行中的導入', () => {
  render(<IngestPanel progress={mk()} rateLine="" />)
  expect(screen.getByText('目前無執行中的導入')).toBeInTheDocument()
})

/*
 * 這個面板原本只認全量 `ingest_all.py`，所以生產實際走的增量匯入在跑時，它照樣
 * 寫「目前無執行中的導入」——2026-08-12 手動補積壓時使用者看到的正是這句，而匯入
 * 明明在跑。管線列已於 #205 補上 `sync_import`，但那是另一個元件，這裡沒跟上。
 */
test('只有增量匯入在跑 → 說出增量匯入，而不是「目前無執行中的導入」', () => {
  render(
    <IngestPanel
      progress={mk({ pipelines: { web: true, ingest: false, sync_import: true, tag: false, summaries: false } })}
      rateLine=""
    />,
  )
  expect(screen.getByText('增量匯入執行中')).toBeInTheDocument()
  expect(screen.queryByText('目前無執行中的導入')).toBeNull()
})

test('增量匯入在跑 → 走不定量條而非靜止條', () => {
  const { container } = render(
    <IngestPanel
      progress={mk({ pipelines: { web: true, ingest: false, sync_import: true, tag: false, summaries: false } })}
      rateLine=""
    />,
  )
  expect(container.querySelector('[class*="indetBar"]')).not.toBeNull()
})

// 全量在跑時它才是這個面板的主角：那條路徑有真實的篇數/失敗數（ingest_run 日誌），
// 增量沒有，拿「增量匯入執行中」蓋掉具體數字是資訊量的倒退。
test('兩條都在跑 → 以全量的篇數為準，不被增量文案蓋掉', () => {
  render(
    <IngestPanel
      progress={mk({
        pipelines: { web: true, ingest: true, sync_import: true, tag: false, summaries: false },
        ingest: { ingested: 42, chunks: 100, fail: 1 },
      })}
      rateLine=""
    />,
  )
  expect(screen.getByText('本輪已導入 42 篇 · 失敗 1')).toBeInTheDocument()
  expect(screen.queryByText('增量匯入執行中')).toBeNull()
})

test('pipelines.ingest true → 不定量條；false → 靜止條', () => {
  const { container: on } = render(<IngestPanel progress={mk({ pipelines: { web: true, ingest: true, tag: false, summaries: false } })} rateLine="" />)
  expect(on.querySelector('[class*="indetBar"]')).not.toBeNull()
  const { container: off } = render(<IngestPanel progress={mk()} rateLine="" />)
  expect(off.querySelector('[class*="indetBar"]')).toBeNull()
  expect(off.querySelector('[class*="indetIdle"]')).not.toBeNull()
})
