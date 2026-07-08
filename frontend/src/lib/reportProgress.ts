import type { ReportStage } from './askSchemas'

const MAP: Record<ReportStage, { pct: number; text: string }> = {
  retrieving: { pct: 20, text: '深度檢索研報中…' },
  searching_web: { pct: 40, text: '搜尋網路補充…' },
  writing: { pct: 50, text: '撰寫研報中…' },
  rendering: { pct: 90, text: '排版 PDF 中…' },
}

export function reportProgress(stage: ReportStage): { pct: number; text: string } {
  return MAP[stage]
}
