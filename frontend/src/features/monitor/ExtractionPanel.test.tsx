import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ExtractionPanel } from './ExtractionPanel'
import type { Extraction } from './progressSchema'

const base: Extraction = {
  target_version: 'ext-2026-09-02.v3',
  versions: [
    { version: '(unknown)', count: 15109 },
    { version: 'ext-2026-09-02.v3', count: 3 },
  ],
  needs_review: 2,
  pages_failed: 0,
  stopped_at: { ingested: 3, skip_admin: 1, extract_error: 1 },
  log_latest: '2026-09-03',
  backfill: { done: 3, total: 15112, remaining: 15109, pct: 0.02, latest: '2026-09-03' },
}

test('回填進度、落點分佈、要人看、版本四列都呈現', () => {
  render(<ExtractionPanel extraction={base} />)
  expect(screen.getByText('抽取品質與回填（目標 ext-2026-09-02.v3）')).toBeInTheDocument()
  expect(screen.getByText('已達目標 3/15112')).toBeInTheDocument()
  expect(screen.getByText('尚餘 15109')).toBeInTheDocument()
  expect(screen.getByText('已入庫 3')).toBeInTheDocument()
  expect(screen.getByText('行政件 1')).toBeInTheDocument()
  expect(screen.getByText('待複核 2')).toBeInTheDocument()
  expect(screen.getByText('(unknown) 15109')).toBeInTheDocument()
  expect(screen.getByText('最後寫入 2026-09-03')).toBeInTheDocument()
})

test('extract_error、待複核、頁級失敗 > 0 才標警示色', () => {
  const { container } = render(<ExtractionPanel extraction={base} />)
  const warn = [...container.querySelectorAll('[class*="fWarn"]')].map(e => e.textContent)
  expect(warn).toContain('抽取失敗 1')
  expect(warn).toContain('待複核 2')
  expect(warn).not.toContain('頁級失敗 0')
  expect(warn).not.toContain('已入庫 3')
})

test('未知的 stopped_at 值原樣顯示，不吞掉', () => {
  render(<ExtractionPanel extraction={{ ...base, stopped_at: { weird_new_state: 4 } }} />)
  expect(screen.getByText('weird_new_state 4')).toBeInTheDocument()
})

test('後端缺表（null）→ 指向 make schema 的降級文案', () => {
  render(<ExtractionPanel extraction={null} />)
  expect(screen.getByText(/schema 尚未套用/)).toBeInTheDocument()
})

test('後端未提供（undefined）→ 舊版後端的降級文案，不整張消失', () => {
  render(<ExtractionPanel extraction={undefined} />)
  expect(screen.getByText('此版後端未提供抽取統計')).toBeInTheDocument()
})
