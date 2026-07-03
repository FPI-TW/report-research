import { describe, it, expect, vi, afterEach } from 'vitest'
import { browseReports, searchReports, getReportFull } from './searchApi'

function mockFetchOnce(body: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true, status: 200, json: async () => body,
  })))
}
afterEach(() => vi.unstubAllGlobals())

describe('searchApi', () => {
  it('browseReports hits /api/reports with params and parses', async () => {
    mockFetchOnce({ total: 0, offset: 0, items: [] })
    const r = await browseReports(new URLSearchParams({ limit: '50', offset: '0' }))
    expect(r.total).toBe(0)
    const url = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(url).toContain('/api/reports?')
    expect(url).toContain('limit=50')
  })
  it('searchReports hits /api/search and parses', async () => {
    mockFetchOnce({ query: 'AI', market: null, total: 0, results: [] })
    const r = await searchReports(new URLSearchParams({ q: 'AI' }))
    expect(r.query).toBe('AI')
    expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain('/api/search?')
  })
  it('getReportFull encodes id and parses has_file', async () => {
    mockFetchOnce({ report_id: 'a/b', file_name: 'x.pdf', market: null, source: null,
      summary: null, report_date: null, report_type: null, has_file: false })
    const f = await getReportFull('a/b')
    expect(f.has_file).toBe(false)
    expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain('/api/report/a%2Fb/full')
  })
})
