import type { Pipelines } from './progressSchema'

/** 派生資產的顯示名，管線列與覆蓋率卡共用同一份。 */
export const DERIVED_LABEL = { takeaways: '重點摘錄', signals: '觀點訊號' } as const

/** 管線列與頁首執行中分母的單一來源。 */
export const PIPELINE_ROWS: { key: keyof Pipelines; name: string }[] = [
  { key: 'web', name: 'Web 服務' },
  { key: 'ingest', name: '報告導入' },
  { key: 'tag', name: '語意標註' },
  { key: 'summaries', name: '摘要生成' },
  { key: 'takeaways', name: DERIVED_LABEL.takeaways },
  { key: 'signals', name: DERIVED_LABEL.signals },
]
