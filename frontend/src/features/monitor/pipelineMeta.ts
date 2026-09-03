import type { Pipelines } from './progressSchema'

/** 派生資產的顯示名，管線列與覆蓋率卡共用同一份。 */
export const DERIVED_LABEL = { takeaways: '重點摘錄', signals: '觀點訊號' } as const

/** 管線列與頁首執行中分母的單一來源。 */
export const PIPELINE_ROWS: { key: keyof Pipelines; name: string }[] = [
  { key: 'web', name: 'Web 服務' },
  { key: 'ingest', name: '報告導入' },
  // 與「報告導入」並列而非取代它：那格是全量 ingest_all.py，這格是生產實際走的
  // 增量路徑 sync_new_reports.py（排程每 3 小時／手動補積壓）。
  { key: 'sync_import', name: '增量匯入' },
  { key: 'tag', name: '語意標註' },
  { key: 'summaries', name: '摘要生成' },
  { key: 'titles', name: '顯示標題' },
  { key: 'takeaways', name: DERIVED_LABEL.takeaways },
  { key: 'signals', name: DERIVED_LABEL.signals },
  // E1d 深夜回填：每晚 01:00 起最多 4 小時，零 LLM。不寫 log 檔，這列是唯一表徵。
  { key: 'backfill', name: '抽取回填' },
]
