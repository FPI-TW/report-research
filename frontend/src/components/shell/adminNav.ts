import type { IconName } from '../primitives/Icon'

/**
 * 管理後台的資訊架構：圖示軌的 7 個領域 → 次級面板的分組 → 頁面。
 *
 * 外殼的圖示軌、次級面板、麵包屑與 ⌘K 頁面搜尋都從這一份算出來；路由本身在 `App.tsx`。
 * `scope` 有值的頁面只對擁有該 scope 的管理員顯示（顯示層；擋人的是後端 `require_scope`）。
 * `requires` 有值的頁面，資料要靠主機上另外安裝的元件；這台主機確定沒裝時不列（判斷在
 * `features/admin/adminCapabilities.ts`）。一個領域底下的頁面全部看不到時，整個領域不顯示。
 */
export type AdminPageKey =
  | 'home'
  | 'reviews' | 'reports' | 'uploads'
  | 'users' | 'quota' | 'apiClients'
  | 'services' | 'dependencies' | 'remoteHosts' | 'incidents'
  | 'pipeline' | 'dataHealth' | 'database'
  | 'jobs' | 'logs' | 'host' | 'diagnostics'
  | 'llmUsage'
  | 'security' | 'audit'
  | 'analytics'
  | 'flags'

export type AdminDomainKey = 'home' | 'content' | 'access' | 'ops' | 'sec' | 'insight' | 'settings'

/**
 * 頁面的資料來源要靠主機上另外安裝的元件。
 *
 *   monitoring  監控匯入器（`report-mark-load-observations`）：把收集器的觀測、批次執行紀錄與事件匯進 DB。
 *               主機、排程工作、事件三頁只讀這份 DB 投影，沒裝的主機上它們永遠是空的。
 */
export type AdminCapability = 'monitoring'

export interface AdminPage { key: AdminPageKey; label: string; to: string; scope?: string; requires?: AdminCapability }
export interface AdminGroup { label?: string; pages: readonly AdminPage[] }
export interface AdminDomain { key: AdminDomainKey; label: string; icon: IconName; groups: readonly AdminGroup[] }

const OPS = '/admin/operations'
const ops = (key: AdminPageKey, label: string, path: string, requires?: AdminCapability): AdminPage =>
  ({ key, label, to: `${OPS}/${path}`, scope: 'ops.read', requires })

export const ADMIN_IA: readonly AdminDomain[] = [
  { key: 'home', label: '總覽', icon: 'compass', groups: [
    { pages: [{ key: 'home', label: '總覽', to: '/admin' }] },
  ] },
  { key: 'content', label: '內容', icon: 'fileText', groups: [
    { pages: [
      { key: 'reviews', label: '待複核', to: '/admin/reviews' },
      { key: 'reports', label: '研報管理', to: '/admin/reports', scope: 'reports.manage' },
      { key: 'uploads', label: '上傳研報', to: '/admin/uploads', scope: 'reports.manage' },
    ] },
  ] },
  { key: 'access', label: '存取', icon: 'user', groups: [
    { pages: [
      { key: 'users', label: '帳號管理', to: '/admin/users' },
      { key: 'quota', label: '配額', to: '/admin/quota', scope: 'accounts.manage' },
      { key: 'apiClients', label: 'API 用戶端', to: '/admin/api-clients', scope: 'api_clients.manage' },
    ] },
  ] },
  { key: 'ops', label: '維運', icon: 'activity', groups: [
    { label: '健康', pages: [
      ops('services', '服務', 'services'), ops('dependencies', '依賴圖', 'dependencies'),
      ops('remoteHosts', '來源主機', 'remote-hosts'), ops('incidents', '事件', 'incidents', 'monitoring'),
    ] },
    { label: '資料', pages: [
      ops('pipeline', '管線', 'pipeline'), ops('dataHealth', '資料健康', 'data-health'), ops('database', '資料庫', 'database'),
    ] },
    { label: '執行', pages: [
      ops('jobs', '排程工作', 'jobs', 'monitoring'), ops('logs', '日誌', 'logs'), ops('host', '主機', 'host', 'monitoring'), ops('diagnostics', '診斷', 'diagnostics'),
    ] },
    { label: '用量', pages: [ops('llmUsage', 'LLM 用量', 'llm-usage')] },
  ] },
  { key: 'sec', label: '安全', icon: 'shield', groups: [
    { pages: [
      { key: 'security', label: '安全', to: '/admin/security', scope: 'audit.read' },
      { key: 'audit', label: '操作紀錄', to: '/admin/audit' },
    ] },
  ] },
  { key: 'insight', label: '洞察', icon: 'trendUp', groups: [
    { pages: [{ key: 'analytics', label: '使用分析', to: '/admin/analytics', scope: 'analytics.read' }] },
  ] },
  { key: 'settings', label: '設定', icon: 'filter', groups: [
    { pages: [{ key: 'flags', label: '功能旗標', to: '/admin/flags', scope: 'ops.read' }] },
  ] },
]

/**
 * 依 scope 濾掉看不到的頁面，再拿掉這台主機確定沒有資料來源的頁面（`missing`）；空的分組與空的領域一併拿掉。
 * `missing` 只放「確定沒裝」的：還不知道（服務清單沒回來、維運代理不通）時不要放，寧可多列也不要把有資料的頁藏起來。
 */
export function visibleDomains(scopes: readonly string[], missing: readonly AdminCapability[] = []): AdminDomain[] {
  return ADMIN_IA
    .map(d => ({
      ...d,
      groups: d.groups
        .map(g => ({
          ...g,
          pages: g.pages.filter(p => (!p.scope || scopes.includes(p.scope)) && !(p.requires && missing.includes(p.requires))),
        }))
        .filter(g => g.pages.length > 0),
    }))
    .filter(d => d.groups.length > 0)
}

export interface AdminLocation { domain: AdminDomain; group: AdminGroup; page: AdminPage }

/** 一頁在不在某個路徑上：總覽只認 `/admin` 本身，其他頁連同子路徑（例如上傳詳情）都算。 */
export function pageMatches(page: AdminPage, pathname: string): boolean {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  if (page.to === '/admin') return path === '/admin'
  return path === page.to || path.startsWith(page.to + '/')
}

/** 目前路徑落在哪個領域／分組／頁面；不在任何一頁上（404、被 scope 濾掉的頁）回 null。 */
export function locate(domains: readonly AdminDomain[], pathname: string): AdminLocation | null {
  for (const domain of domains) {
    for (const group of domain.groups) {
      const page = group.pages.find(p => pageMatches(p, pathname))
      if (page) return { domain, group, page }
    }
  }
  return null
}

export interface AdminPageEntry { page: AdminPage; domain: AdminDomain; group: AdminGroup; path: string }

/** 攤平成頁面清單（⌘K 搜尋、總覽的捷徑用）；`path` 是「領域 / 分組」。 */
export function flattenPages(domains: readonly AdminDomain[]): AdminPageEntry[] {
  return domains.flatMap(domain => domain.groups.flatMap(group => group.pages.map(page => ({
    page, domain, group, path: [domain.label, group.label].filter(Boolean).join(' / '),
  }))))
}

/** 頁面搜尋：比對頁名與「領域 / 分組」，不分大小寫；空字串回全部。 */
export function searchPages(entries: readonly AdminPageEntry[], query: string): AdminPageEntry[] {
  const q = query.trim().toLowerCase()
  if (!q) return [...entries]
  return entries.filter(e => e.page.label.toLowerCase().includes(q) || e.path.toLowerCase().includes(q))
}
