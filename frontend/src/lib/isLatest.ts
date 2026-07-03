import type { ReportRow } from './schemas'

function normDate(d: string | null | undefined): string | null {
  return d && /^\d{4}-\d{2}-\d{2}/.test(d) ? d.slice(0, 10) : null
}

/**
 * 鏡像 app/services/answer.py：已載入結果集中 report_date 嚴格最大者的 id。
 * 嚴格大於（`>`）→ 同日保留最先出現者；無日期者一律不參與；全無→null。
 */
export function latestId(rows: Pick<ReportRow, 'report_id' | 'report_date'>[]): string | null {
  let bestId: string | null = null
  let bestDate: string | null = null
  for (const r of rows) {
    const d = normDate(r.report_date)
    if (d !== null && (bestDate === null || d > bestDate)) {
      bestDate = d
      bestId = r.report_id
    }
  }
  return bestId
}
