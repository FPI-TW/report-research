import { getJSON } from './api'
import {
  reportListResponseSchema, searchResponseSchema, reportFullSchema,
  type ReportListResponse, type SearchResponse, type ReportFull,
} from './schemas'

/** browse 模式：無關鍵字列出報告。params 由 searchFilters.toApiParams 產出。 */
export function browseReports(params: URLSearchParams): Promise<ReportListResponse> {
  return getJSON(`/api/reports?${params.toString()}`, reportListResponseSchema, { cache: 'no-store' })
}

/** search 模式：語意檢索。params 需含 q。 */
export function searchReports(params: URLSearchParams): Promise<SearchResponse> {
  return getJSON(`/api/search?${params.toString()}`, searchResponseSchema, { cache: 'no-store' })
}

/** 詳情 modal：取單篇 metadata + has_file。 */
export function getReportFull(id: string): Promise<ReportFull> {
  return getJSON(`/api/report/${encodeURIComponent(id)}/full`, reportFullSchema, { cache: 'no-store' })
}
