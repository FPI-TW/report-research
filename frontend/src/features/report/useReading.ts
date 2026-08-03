import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../../lib/api'
import { getReadingDoc, getReadingText, getSimilarReports } from '../../lib/readingApi'

/** 閱讀頁骨架；hash 無效時不打 API。 */
export function useReadingDoc(hash: string, enabled = true) {
  return useQuery({
    queryKey: ['reading-doc', hash],
    queryFn: () => getReadingDoc(hash),
    enabled: enabled && Boolean(hash),
    retry: false,
  })
}

/**
 * 正典文字：只有內嵌不了原始 PDF（.docx／無檔）時才抓。
 * PDF 研報永遠不會啟用這支查詢 —— 呼叫端見 ReportPage 的 pdfViewable。
 */
export function useReadingText(hash: string, enabled: boolean) {
  return useQuery({
    queryKey: ['reading-text', hash],
    queryFn: () => getReadingText(hash),
    enabled: enabled && Boolean(hash),
    staleTime: 5 * 60_000,
    retry: false,
  })
}

export function useSimilarReports(hash: string, enabled = true) {
  return useQuery({
    // 版面就是 4 欄（見 SimilarReports.module.css）：抓 4 就好，不多抓兩筆丟掉。
    queryKey: ['reading-similar', hash],
    queryFn: () => getSimilarReports(hash, 4),
    enabled: enabled && Boolean(hash),
    staleTime: 60_000,
    retry: false,
  })
}

/** 404＝查無此研報；其他錯誤留給 UI 重試。 */
export function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404
}
