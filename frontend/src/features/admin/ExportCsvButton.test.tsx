import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ExportCsvButton } from './ExportCsvButton'
import { filenameFrom } from './exportCsv'

const created: string[] = []
let clicked: HTMLAnchorElement[] = []

beforeEach(() => {
  created.length = 0
  clicked = []
  vi.stubGlobal('URL', Object.assign(URL, {
    createObjectURL: vi.fn(() => { created.push('blob:1'); return 'blob:1' }),
    revokeObjectURL: vi.fn(),
  }))
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    clicked.push(this)
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function csvResponse(rows: number, truncated = false) {
  return new Response('﻿id\r\n1\r\n', {
    status: 200,
    headers: {
      'Content-Type': 'text/csv; charset=utf-8',
      'Content-Disposition': 'attachment; filename="report-mark-audit-20261006.csv"',
      'X-Export-Rows': String(rows),
      'X-Export-Truncated': truncated ? 'true' : 'false',
    },
  })
}

test('下載成功：用後端給的檔名、說明筆數；fetch 帶 same-origin 與 no-store', async () => {
  const fetchMock = vi.fn(async () => csvResponse(2))
  vi.stubGlobal('fetch', fetchMock)
  render(<ExportCsvButton href="/api/admin/export/audit.csv" what="操作紀錄" />)
  fireEvent.click(screen.getByRole('button', { name: '匯出 CSV' }))
  expect(await screen.findByRole('status')).toHaveTextContent('已下載 2 筆')
  expect(fetchMock).toHaveBeenCalledWith('/api/admin/export/audit.csv', { credentials: 'same-origin', cache: 'no-store' })
  expect(clicked).toHaveLength(1)
  expect(clicked[0].download).toBe('report-mark-audit-20261006.csv')
  expect(created).toEqual(['blob:1'])
})

test('達上限時註明只含最新的那幾筆', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => csvResponse(10000, true)))
  render(<ExportCsvButton href="/api/admin/export/reports.csv" what="研報清單" />)
  fireEvent.click(screen.getByRole('button', { name: '匯出 CSV' }))
  expect(await screen.findByRole('status')).toHaveTextContent('達匯出上限，只含最新的 10000 筆')
})

test('失敗時原樣顯示後端 detail，不觸發下載', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(
    JSON.stringify({ detail: '無法寫入稽核紀錄，這次沒有匯出；請稍後再試', code: 'export_audit_failed' }), { status: 503 },
  )))
  render(<ExportCsvButton href="/api/admin/export/users.csv" what="帳號清單" />)
  fireEvent.click(screen.getByRole('button', { name: '匯出 CSV' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('帳號清單匯出失敗：無法寫入稽核紀錄')
  expect(clicked).toHaveLength(0)
})

test('filenameFrom：拿不到檔名時用 fallback', () => {
  expect(filenameFrom('attachment; filename="a.csv"', 'x.csv')).toBe('a.csv')
  expect(filenameFrom(null, 'x.csv')).toBe('x.csv')
})
