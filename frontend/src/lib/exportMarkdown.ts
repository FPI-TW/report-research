import type { AnswerView } from './askReducer'
import type { Source } from './askSchemas'
import type { z } from 'zod'
import type { briefPayloadSchema } from './briefSchemas'
import { displayTitle } from './displayTitle'

/**
 * 把站內內容組成「貼到別處仍然自足」的 Markdown。
 *
 * 研究員的工作流是看完 → 貼進自己的報告。回答本文裡的 `[1]`、`[2]` 是引用標記，
 * 只複製本文的話貼出去之後那些數字沒有任何意義——讀的人不知道 [1] 是哪一份研報。
 * 這裡做的事只有一件：把畫面上本來就看得到的來源清單跟著本文一起帶走。
 *
 * 全部是純函式、不碰 DOM 與剪貼簿（複製走 lib/clipboard.ts，區網 HTTP 的限制在那裡處理）。
 */

type BriefPayload = z.infer<typeof briefPayloadSchema>

const CITE_RE = /\[(\d+)\]/g

/** 本文實際引用到的編號（依首次出現的順序）。 */
export function citedNumbers(answer: string): number[] {
  const seen = new Set<number>()
  for (const m of answer.matchAll(CITE_RE)) seen.add(Number(m[1]))
  return [...seen]
}

function sourceLine(s: Source): string {
  const meta = [s.market, s.report_date].filter(Boolean).join('，')
  return `[${s.n}] ${displayTitle(s)}${meta ? `（${meta}）` : ''}`
}

/**
 * 回答本文＋來源清單。
 *
 * 只列**本文引用到的**來源：檢索會帶回約 15 份，回答通常只引其中幾份，全列出來會讓
 * 貼出去的腳註比本文還長，而且暗示「這些都是依據」。本文一個引用標記都沒有時
 * （例如只答了一句、或網搜回答）才退回列全部，免得來源整個不見。
 */
export function answerMarkdown(view: Pick<AnswerView, 'answer' | 'sources' | 'extSources'>): string {
  const body = view.answer.trim()
  const cited = new Set(citedNumbers(body))
  const picked = cited.size > 0 ? view.sources.filter(s => cited.has(s.n)) : view.sources
  const blocks = [body]
  if (picked.length > 0) {
    blocks.push(['**來源研報**', ...[...picked].sort((a, b) => a.n - b.n).map(sourceLine)].join('\n'))
  }
  if (view.extSources.length > 0) {
    blocks.push(['**外部參考**', ...view.extSources.map(e => `- [${e.title}](${e.url})`)].join('\n'))
  }
  return blocks.join('\n\n')
}

/** 每日簡報：標題＋窗期＋本文＋來源研報清單（含未列入彙整的差額）。 */
export function briefMarkdown(brief: BriefPayload): string {
  const blocks = [`# 每日研報簡報 ${brief.brief_date}`, brief.markdown.trim()]
  if (brief.reports.length > 0) {
    const lines = brief.reports.map(r => {
      const meta = [r.source_display ?? r.source, r.report_date].filter(Boolean).join('，')
      return `- ${displayTitle(r)}${meta ? `（${meta}）` : ''}`
    })
    const more = brief.report_count - brief.reports.length
    if (more > 0) lines.push(`- 另有 ${more} 篇未列入本期彙整`)
    blocks.push(['## 本期來源研報', ...lines].join('\n'))
  }
  return blocks.join('\n\n')
}
