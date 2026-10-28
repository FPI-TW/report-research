import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../../lib/api'
import { getBrokerHistory, getInstrumentRadar } from '../../lib/radarApi'
import type { Market } from '../../lib/radarSchemas'
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

/** 版面直接列出 3 篇（交接包「閱讀頁」第 6 點）：抓 3 就好，不多抓再丟掉。 */
export const SIMILAR_LIMIT = 3

export function useSimilarReports(hash: string, enabled = true) {
  return useQuery({
    queryKey: ['reading-similar', hash],
    queryFn: () => getSimilarReports(hash, SIMILAR_LIMIT),
    enabled: enabled && Boolean(hash),
    staleTime: 60_000,
    retry: false,
  })
}

/**
 * 評等卡連往的券商共識期間。與券商共識詳情頁的預設期間相同，「N 家券商共識」說的
 * 才是點過去看到的那個數字。
 */
export const CONSENSUS_WINDOW = '90' as const

/**
 * 評等卡右上的「N 家券商共識」。
 *
 * queryKey 與券商共識頁的 `useInstrumentRadar` 相同，點過去時直接命中快取。
 * 這是**派生資訊**：失敗或還在載入時卡片照常顯示，只是少了券商數（fail-open）。
 */
export function useConsensusCount(code: string, market: Market | null) {
  return useQuery({
    queryKey: ['radar-overview', market ?? '', code, CONSENSUS_WINDOW],
    queryFn: ({ signal }) => getInstrumentRadar(code, market as Market, CONSENSUS_WINDOW, { signal }),
    enabled: Boolean(market && code),
    staleTime: 60_000,
    retry: false,
    select: data => data.coverage.brokers_in_consensus,
  })
}

/**
 * 本篇目標價相對同一券商前一篇的調幅（「↑ 上調 8%」）。
 *
 * 可比與否、前一篇是哪一篇，**全由後端決定**（`build_broker_history` 的相鄰差異，
 * `diffs[i]` 對應 `snapshots[i]`），前端只負責找出本篇那一筆。不可比、沒有前一篇、
 * 或查詢失敗一律回 null，卡片就不顯示調幅。
 */
export function useTargetRevision(
  code: string,
  market: Market | null,
  broker: string | null | undefined,
  reportId: string,
) {
  return useQuery({
    queryKey: ['radar-broker', market ?? '', code, broker ?? '', CONSENSUS_WINDOW],
    queryFn: ({ signal }) =>
      getBrokerHistory(code, broker as string, market as Market, CONSENSUS_WINDOW, { signal }),
    enabled: Boolean(market && code && broker),
    staleTime: 60_000,
    retry: false,
    select: (history) => {
      const i = history.snapshots.findIndex(s => s.report_id === reportId)
      const change = i >= 0
        ? history.diffs[i]?.changes.find(c => c.field === 'target_price')
        : undefined
      if (!change?.comparable || change.pct_change == null) return null
      if (change.direction !== 'up' && change.direction !== 'down') return null
      return { direction: change.direction, pct: change.pct_change }
    },
  })
}

/** 404＝查無此研報；其他錯誤留給 UI 重試。 */
export function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404
}
