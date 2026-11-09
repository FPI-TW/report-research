import type { Pipelines } from '../../../../lib/progressSchema'
import { t } from '@/i18n'

/** 派生資產的顯示名，管線列與覆蓋率卡共用同一份。 */
export const DERIVED_LABEL = { get takeaways() { return t('admin.ops.pipeline.pipelineMeta.keyTakeaways') }, get signals() { return t('admin.ops.pipeline.pipelineMeta.viewpointSignals') } } as const

/** 各批次管線的顯示名與順序（管線分頁的「批次」列）。 */
export const PIPELINE_ROWS: { key: keyof Pipelines; name: string }[] = [
  { key: 'web', get name() { return t('admin.ops.pipeline.pipelineMeta.webService') } },
  // 全量 ingest_all.py（只在初次建庫或補跑歷史時跑）與生產實際走的增量路徑
  // sync_new_reports.py（排程每 3 小時／手動補積壓）並列，兩者不可合併。
  { key: 'ingest', get name() { return t('admin.ops.pipeline.pipelineMeta.fullIngest') } },
  { key: 'sync_import', get name() { return t('admin.ops.pipeline.pipelineMeta.incrementalImport') } },
  { key: 'tag', get name() { return t('admin.ops.pipeline.pipelineMeta.semanticTagging') } },
  { key: 'summaries', get name() { return t('admin.ops.pipeline.pipelineMeta.summaryGeneration') } },
  { key: 'titles', get name() { return t('admin.ops.pipeline.pipelineMeta.displayTitles') } },
  { key: 'takeaways', name: DERIVED_LABEL.takeaways },
  { key: 'signals', name: DERIVED_LABEL.signals },
  // E1d 深夜回填：每晚 01:00 起最多 4 小時，零 LLM。不寫 log 檔，這列是唯一表徵。
  { key: 'backfill', get name() { return t('admin.ops.pipeline.pipelineMeta.extractionBackfill') } },
]
