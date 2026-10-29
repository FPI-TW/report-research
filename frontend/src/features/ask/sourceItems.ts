import { displayTitle } from '../../lib/displayTitle'
import type { AnswerView } from '../../lib/askReducer'

/** 來源卡片要的欄位：研報與網路補充排成同一份清單，編號沿用回答裡的 [n]。 */
export type SourceItem =
  | { kind: 'report'; n: number; title: string; market: string; date: string | null; reportId: string }
  | { kind: 'web'; n: number; title: string; host: string; url: string }

function hostOf(url: string): string {
  try { return new URL(url).hostname } catch { return url }
}

export function sourceItems(view: AnswerView): SourceItem[] {
  const reports: SourceItem[] = view.sources.map(s => ({
    kind: 'report', n: s.n, title: displayTitle(s), market: s.market, date: s.report_date, reportId: s.report_id,
  }))
  // 網路補充接在研報之後編號（與後端組 [n] 的順序一致）。
  const web: SourceItem[] = view.extSources.map((e, i) => ({
    kind: 'web', n: view.sources.length + i + 1, title: e.title, host: hostOf(e.url), url: e.url,
  }))
  return [...reports, ...web]
}
