import type { ReviewItem } from '../../lib/reviewSchemas'

function flagNumber(item: ReviewItem, key: string): number | null {
  const v = item.quality_flags?.[key]
  return typeof v === 'number' ? v : null
}

/**
 * 「為什麼要看」，連同量到的值。
 *
 * 先前這一列只顯示品質分數，而全庫被標到的研報分數多在 0.87–0.93——它們是 coverage 或
 * 亂碼率過線，不是分數低。一排不低的紅色數字，看不出任何一篇為什麼在這裡。
 * 判定在後端（store.review_reasons，與入庫時同一份邏輯），這裡只負責說成人話。
 */
export function reasonText(item: ReviewItem, reason: string): string {
  switch (reason) {
    case 'pages_failed': return `${item.pages_failed?.length ?? 0} 頁抽取失敗`
    case 'low_score': return `品質分數 ${item.quality_score?.toFixed(2) ?? '—'}`
    case 'low_coverage': {
      const v = flagNumber(item, 'layout_coverage')
      return v === null ? '版面覆蓋率過低' : `版面覆蓋率 ${(v * 100).toFixed(0)}%`
    }
    case 'garbled': {
      const v = flagNumber(item, 'garbled_ratio')
      return v === null ? '亂碼率過高' : `亂碼率 ${(v * 100).toFixed(1)}%`
    }
    default: return reason
  }
}
