import type { z } from 'zod'
import type { briefPayloadSchema } from './briefSchemas'
import { displayTitle } from './displayTitle'
import { t } from '@/i18n'

/**
 * 把站內內容組成「貼到別處仍然自足」的 Markdown。
 *
 * 純函式、不碰 DOM 與剪貼簿（複製走 lib/clipboard.ts，區網 HTTP 的限制在那裡處理）。
 *
 * 問答的「複製回答」刻意不走這裡：它只複製本文，不附來源清單。
 */

type BriefPayload = z.infer<typeof briefPayloadSchema>

/** 每日簡報：標題＋本文＋來源研報清單（含未列入彙整的差額）。 */
export function briefMarkdown(brief: BriefPayload): string {
  const blocks = [t('lib.exportMarkdown.dailyResearchBrief', { brief_date: brief.brief_date }), brief.markdown.trim()]
  if (brief.reports.length > 0) {
    const lines = brief.reports.map(r => {
      const meta = [r.source_display ?? r.source, r.report_date].filter(Boolean).join(t('lib.exportMarkdown.text'))
      return `- ${displayTitle(r)}${meta ? t('lib.exportMarkdown.text2', { meta }) : ''}`
    })
    const more = brief.report_count - brief.reports.length
    if (more > 0) lines.push(t('lib.exportMarkdown.moreReportsNotIncludedIn', { more }))
    blocks.push([t('lib.exportMarkdown.sourceReports'), ...lines].join('\n'))
  }
  return blocks.join('\n\n')
}
