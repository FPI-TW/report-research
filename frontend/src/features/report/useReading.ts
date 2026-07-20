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
 * 正典文字：只有切到文字檢視才抓（PDF 是預設檢視，用不到）。
 *
 * chunk 會改變回應內容（後端一併回該段的字元 offset），故必須進 queryKey ——
 * 否則換了 chunk 會拿到上一段的命中位置。
 */
export function useReadingText(hash: string, enabled: boolean, chunk: number | null = null) {
  return useQuery({
    queryKey: ['reading-text', hash, chunk],
    queryFn: () => getReadingText(hash, chunk),
    enabled: enabled && Boolean(hash),
    staleTime: 5 * 60_000,
    retry: false,
  })
}

export function useSimilarReports(hash: string, enabled = true) {
  return useQuery({
    queryKey: ['reading-similar', hash],
    queryFn: () => getSimilarReports(hash, 6),
    enabled: enabled && Boolean(hash),
    staleTime: 60_000,
    retry: false,
  })
}

/** 404＝查無此研報；其他錯誤留給 UI 重試。 */
export function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404
}
