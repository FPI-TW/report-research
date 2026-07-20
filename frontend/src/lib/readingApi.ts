import { getJSON } from './api'
import {
  readingDocSchema,
  readingTextSchema,
  similarResponseSchema,
  type ReadingDoc,
  type ReadingText,
  type SimilarResponse,
} from './readingSchemas'

/** 閱讀頁骨架（不含全文）。 */
export function getReadingDoc(fileHash: string): Promise<ReadingDoc> {
  return getJSON(`/api/reading/${encodeURIComponent(fileHash)}`, readingDocSchema, {
    cache: 'no-store',
  })
}

/**
 * 正典文字；所有 takeaway offset 皆以此字串為準。
 *
 * 帶 chunk（檢索命中的 chunk_index）時，後端一併回該段的 chunk_start/chunk_end ——
 * 命中定位由後端 anchor.py 算，前端不重造比對邏輯。
 */
export function getReadingText(fileHash: string, chunk?: number | null): Promise<ReadingText> {
  const qs = chunk == null ? '' : `?${new URLSearchParams({ chunk: String(chunk) })}`
  return getJSON(`/api/reading/${encodeURIComponent(fileHash)}/text${qs}`, readingTextSchema, {
    cache: 'no-store',
  })
}

export function getSimilarReports(fileHash: string, limit = 6): Promise<SimilarResponse> {
  const sp = new URLSearchParams({ limit: String(limit) })
  return getJSON(`/api/reading/${encodeURIComponent(fileHash)}/similar?${sp}`, similarResponseSchema, {
    cache: 'no-store',
  })
}

/** 原始檔走既有端點（以 ReadingDoc 回的 report_id，非 file_hash）。 */
export function reportFileHref(reportId: string): string {
  return `/api/report/${encodeURIComponent(reportId)}/file`
}
