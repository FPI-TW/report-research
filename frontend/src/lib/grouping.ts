import type { ReportRow } from './schemas'
import { t } from '@/i18n'

export interface MonthGroup {
  key: string
  title: string
  count: number
  items: ReportRow[]
}

const UNDATED = '__undated__'

function monthKey(d: string | null | undefined): string {
  return d && /^\d{4}-\d{2}/.test(d) ? d.slice(0, 7) : UNDATED
}

/** 依 report_date 前 7 碼（YYYY-MM）分組；組序沿輸入順序（已由 sort 決定），未標日期置底。 */
export function monthGroups(rows: ReportRow[]): MonthGroup[] {
  const order: string[] = []
  const map = new Map<string, ReportRow[]>()
  for (const r of rows) {
    const k = monthKey(r.report_date)
    if (!map.has(k)) { map.set(k, []); order.push(k) }
    map.get(k)!.push(r)
  }
  const keys = order.filter(k => k !== UNDATED)
  if (map.has(UNDATED)) keys.push(UNDATED)
  return keys.map(k => {
    const items = map.get(k)!
    return {
      key: k,
      title: k === UNDATED ? t('lib.grouping.undated') : t('lib.grouping.text', { slice: k.slice(0, 4), Number: Number(k.slice(5, 7)) }),
      count: items.length,
      items,
    }
  })
}
