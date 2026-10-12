import { useCallback } from 'react'
import { useSearchParams } from 'react-router'
import { reviewKindSchema, reviewStatusSchema, type ReviewKind } from '../../../lib/reviewSchemas'
import type { StatusFilter } from './reviewItem'

const DEFAULT_KIND: ReviewKind = 'faithfulness'
const DEFAULT_STATUS: StatusFilter = 'open'

function parseStatus(raw: string | null): StatusFilter {
  if (raw === 'all') return 'all'
  const parsed = reviewStatusSchema.safeParse(raw)
  return parsed.success ? parsed.data : DEFAULT_STATUS
}

/**
 * 待複核頁放在 URL query 的三個狀態：`?kind=`（分頁）、`?status=`（狀態篩選）、`?review=<id>`（選中的那筆）。
 * 預設值（忠實度低分、待處理）不寫進 URL；認不得的值當成預設。
 *
 * 沒有用共用的 `useQueryParam`：換分頁或換狀態要同時清掉 `review`，而它一次只改一個鍵，
 * 連呼叫兩次後一次會蓋掉前一次。換分頁、換狀態各多一筆瀏覽紀錄；在清單裡換選取則取代目前這一筆，
 * 免得逐筆看完之後要按很多次上一頁才離得開。
 */
export function useReviewParams() {
  const [params, setParams] = useSearchParams()
  const kindParsed = reviewKindSchema.safeParse(params.get('kind'))
  const kind = kindParsed.success ? kindParsed.data : DEFAULT_KIND
  const status = parseStatus(params.get('status'))
  const review = params.get('review')

  const patch = useCallback((changes: Record<string, string | null>, replace: boolean) => {
    setParams(prev => {
      const next = new URLSearchParams(prev)
      for (const [key, value] of Object.entries(changes)) {
        if (value) next.set(key, value)
        else next.delete(key)
      }
      return next
    }, { replace })
  }, [setParams])

  const setKind = useCallback((next: ReviewKind) => {
    patch({ kind: next === DEFAULT_KIND ? null : next, review: null }, false)
  }, [patch])
  const setStatus = useCallback((next: StatusFilter) => {
    patch({ status: next === DEFAULT_STATUS ? null : next, review: null }, false)
  }, [patch])
  const select = useCallback((id: string | null) => {
    patch({ review: id }, true)
  }, [patch])

  return { kind, status, review, setKind, setStatus, select }
}
