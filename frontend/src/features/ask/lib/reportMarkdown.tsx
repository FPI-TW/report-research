import { Fragment, type ReactNode } from 'react'
import { KpiCards } from '../components/KpiCards'
import { ReportChart } from '../components/ReportChart'
import { renderMarkdown } from './markdown'
import { parseReportSegments } from './reportBlocks'

/**
 * 深度研報渲染：交錯 markdown 文字段、KPI 卡、圖表。
 * 對齊 app/services/pdf.py 的區塊順序語意（segments 依原文順序排列）。
 * 報告預覽無可點引用，故 renderMarkdown 一律 maxCite=0。
 */
export function renderReport(markdown: string): ReactNode[] {
  return parseReportSegments(markdown).map((segment, i) => {
    const key = `seg-${i}`
    if (segment.kind === 'kpi') {
      return <KpiCards key={key} block={segment.block} />
    }
    if (segment.kind === 'chart') {
      return <ReportChart key={key} block={segment.block} />
    }
    return <Fragment key={key}>{renderMarkdown(segment.text, 0)}</Fragment>
  })
}
