import { describe, expect, it } from 'vitest'
import { routes } from '../../App'
import { ADMIN_IA, flattenPages, locate, pageMatches, searchPages, visibleDomains } from './adminNav'

const ALL = ['accounts.manage', 'audit.read', 'review.manage', 'ops.read', 'reports.manage', 'analytics.read', 'api_clients.manage']

/** App.tsx 的 /admin 路由樹攤平成路徑（不含導向用的 `*`）。 */
function adminRoutePaths(): string[] {
  type R = { path?: string; index?: boolean; children?: R[] }
  const admin = (routes as R[]).find(r => r.path === '/admin')!
  const out: string[] = []
  const walk = (node: R, base: string) => {
    const here = node.index ? base : node.path ? `${base}/${node.path}` : base
    if (node.index || node.path) out.push(here)
    node.children?.forEach(c => walk(c, here))
  }
  admin.children!.forEach(c => walk(c, '/admin'))
  return out
}

describe('管理後台的資訊架構', () => {
  it('七個領域，順序固定', () => {
    expect(ADMIN_IA.map(d => d.label)).toEqual(['總覽', '內容', '存取', '維運', '安全', '洞察', '設定'])
  })

  it('每一頁只出現一次，路徑不重複', () => {
    const pages = flattenPages(ADMIN_IA).map(e => e.page)
    expect(new Set(pages.map(p => p.key)).size).toBe(pages.length)
    expect(new Set(pages.map(p => p.to)).size).toBe(pages.length)
  })

  it('導覽裡的每一頁在 App.tsx 都有路由', () => {
    const paths = new Set(adminRoutePaths())
    const missing = flattenPages(ADMIN_IA).map(e => e.page.to).filter(to => !paths.has(to))
    expect(missing).toEqual([])
  })

  it('依 scope 濾頁面；整個領域都看不到時拿掉領域', () => {
    expect(visibleDomains([]).map(d => d.key)).toEqual(['home', 'content', 'access', 'sec'])
    expect(flattenPages(visibleDomains([])).map(e => e.page.key)).toEqual(['home', 'reviews', 'users', 'audit'])
    expect(flattenPages(visibleDomains(ALL))).toHaveLength(flattenPages(ADMIN_IA).length)
  })

  it('locate：總覽只認 /admin 本身，其他頁連同子路徑', () => {
    const d = visibleDomains(ALL)
    expect(locate(d, '/admin')?.page.key).toBe('home')
    expect(locate(d, '/admin/')?.page.key).toBe('home')
    expect(locate(d, '/admin/uploads/u-1')?.page.key).toBe('uploads')
    expect(locate(d, '/admin/operations/services/web')).toMatchObject({
      domain: { key: 'ops' }, group: { label: '健康' }, page: { key: 'services' },
    })
    expect(locate(d, '/admin/nope')).toBeNull()
    // 被 scope 濾掉的頁不算在任何一頁上
    expect(locate(visibleDomains([]), '/admin/operations/logs')).toBeNull()
  })

  it('pageMatches 不會把前綴相同的別頁算進來', () => {
    const reports = flattenPages(ADMIN_IA).find(e => e.page.key === 'reports')!.page
    expect(pageMatches(reports, '/admin/reports')).toBe(true)
    expect(pageMatches(reports, '/admin/reports-archive')).toBe(false)
  })

  it('頁面搜尋比對頁名與「領域 / 分組」，不分大小寫', () => {
    const all = flattenPages(visibleDomains(ALL))
    expect(searchPages(all, '')).toHaveLength(all.length)
    expect(searchPages(all, 'llm').map(e => e.page.key)).toEqual(['llmUsage'])
    expect(searchPages(all, 'api').map(e => e.page.key)).toEqual(['apiClients'])
    expect(searchPages(all, '資料').map(e => e.page.key)).toEqual(['pipeline', 'dataHealth', 'database'])
    expect(searchPages(all, '  ')).toHaveLength(all.length)
    expect(searchPages(all, '沒有這頁')).toEqual([])
  })
})
