import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { requestJSON } from '../../lib/api'
import { auditEntrySchema, auditPageSchema, type AuditEntry, type AuditPage } from '../../lib/adminSchemas'
import { auditCategoryQuery, type AuditFilter } from './auditCategories'
import { AUDIT_KEY, AUDIT_PAGE_SIZE } from './useAdmin'

function auditUrl(offset: number, filter: AuditFilter): string {
  const params = new URLSearchParams({ limit: String(AUDIT_PAGE_SIZE), offset: String(offset) })
  const { prefix = [], exclude_prefix = [] } = auditCategoryQuery(filter)
  prefix.forEach(p => params.append('prefix', p))
  exclude_prefix.forEach(p => params.append('exclude_prefix', p))
  return `/api/admin/audit?${params}`
}

/**
 * 管理操作稽核的一頁（AUDIT_PAGE_SIZE 筆），分類交給後端篩：`total` 與翻頁都是篩選後的。
 * 掛在 `AUDIT_KEY` 底下，各頁寫入後既有的 invalidate 會順帶重抓。翻頁、換分類時保留上一份，避免表格閃空。
 *
 * 舊版後端不認得前綴參數，回的是沒篩的一頁；用 `isServerFiltered` 判斷。
 */
export function useAuditLog(offset: number, filter: AuditFilter) {
  return useQuery<AuditPage>({
    queryKey: [...AUDIT_KEY, offset, filter],
    queryFn: () => requestJSON(auditUrl(offset, filter), auditPageSchema, { cache: 'no-store' }),
    placeholderData: keepPreviousData,
    retry: false,
  })
}

/** 後端有沒有照前綴篩：新版一律把套用的前綴放進回應（沒帶是空陣列），舊版沒有這個鍵。 */
export function isServerFiltered(page: AuditPage): boolean {
  return page.prefix != null
}

/** `?entry=` 的值 → 稽核編號（正整數的字串）；其他一律 null，不拿去打 API。 */
export function parseEntryId(raw: string | null): string | null {
  return raw != null && /^[1-9]\d{0,14}$/.test(raw) ? raw : null
}

/**
 * 單筆稽核紀錄（`GET /api/admin/audit/{entry_id}`）：`?entry=` 指的那筆不在目前這一頁時用。
 * 不存在是 404（`ApiError.status`）。同樣掛在 `AUDIT_KEY` 底下。
 */
export function useAuditEntry(id: string | null, enabled: boolean) {
  return useQuery<AuditEntry>({
    queryKey: [...AUDIT_KEY, 'entry', id],
    queryFn: () => requestJSON(`/api/admin/audit/${id}`, auditEntrySchema, { cache: 'no-store' }),
    enabled: enabled && id != null,
    staleTime: 60_000,
    retry: false,
  })
}
