import { useCallback, useState } from 'react'

/**
 * 管理清單的勾選狀態（研報頁、帳號頁共用）。只記 id；「全選」只選呼叫端給的那一組（目前這一頁、
 * 可勾選的列），換頁或改篩選時由呼叫端 `clear()`——批次只作用在看得到的列，不跨頁累積。
 */
export function useSelection() {
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set())
  const toggle = useCallback((id: string) => setSelected(cur => {
    const next = new Set(cur)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  }), [])
  const setAll = useCallback((ids: readonly string[], on: boolean) => setSelected(cur => {
    const next = new Set(cur)
    for (const id of ids) {
      if (on) next.add(id)
      else next.delete(id)
    }
    return next
  }), [])
  const clear = useCallback(() => setSelected(new Set()), [])
  return { selected, toggle, setAll, clear }
}

export type BulkOutcome = {
  /** 例如「批次隱藏」。 */
  title: string
  ok: number
  unchanged?: number
  /** 被規則略過的列：名稱（標題或帳號）與後端給的原因。 */
  skipped: { name: string; detail: string }[]
}

/** 確認對話框裡的對象清單：最多列 `max` 個名稱，其餘以「等 N 筆」帶過。 */
export function namesPreview(names: readonly string[], max = 5): string {
  const head = names.slice(0, max).map(n => `「${n}」`).join('、')
  return names.length > max ? `${head} 等 ${names.length} 筆` : head
}
