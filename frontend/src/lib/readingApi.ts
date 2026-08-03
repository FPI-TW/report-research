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
 * 正典文字；內嵌不了原始檔時的閱讀來源。
 *
 * 端點仍支援 `?chunk=`（回該段的字元 offset），但前端已無命中定位的落點，故不帶。
 */
export function getReadingText(fileHash: string): Promise<ReadingText> {
  return getJSON(`/api/reading/${encodeURIComponent(fileHash)}/text`, readingTextSchema, {
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
