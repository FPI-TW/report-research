import { useEffect, useState } from 'react'
import { needleLadder } from './quoteNeedle'

/** 有引文的摘錄（縮圖列的金色編號只標得出有引文的那幾條）。 */
export interface MarkSource {
  ordinal: number
  quote: string
}

interface EngineSearchTask {
  toPromise: () => Promise<{ results: { pageIndex: number }[]; total: number }>
  abort?: (reason: unknown) => void
}

/** 只取這裡用得到的那一支，免得綁死引擎的完整型別。 */
interface SearchEngine {
  searchAllPages: (doc: unknown, keyword: string) => EngineSearchTask
}

function isSearchEngine(engine: unknown): engine is SearchEngine {
  return typeof (engine as SearchEngine | null)?.searchAllPages === 'function'
}

/** 文件就緒後等一下再開始：先讓第一頁與縮圖渲染完，搜尋排在後面（引擎佇列本來就給它低優先）。 */
const START_DELAY_MS = 400

/**
 * 各頁含哪幾條重點摘錄（pageIndex → 編號），給縮圖列的金色編號用。
 *
 * **直接呼叫引擎，刻意不走搜尋外掛**：外掛每份文件只有一個 query/results（工具列搜尋與
 * 摘錄跳轉共用的那一個），背景預先找頁會把讀者正在看的高亮與計數洗掉。引擎的
 * `searchAllPages` 只回結果、不碰外掛狀態，而且進的是同一條工作佇列（低優先），不會跟
 * 渲染搶。
 *
 * 關鍵字階梯與跳轉完全相同（`needleLadder`、取第一個命中），所以縮圖上的編號與點摘錄
 * 跳到的那一頁一定是同一頁。找不到的那條不標（交接包：「找不到原文的那條不標」）。
 *
 * 這是**派生資訊**：引擎不支援、搜尋失敗都只是少了編號，不影響閱讀（fail-open）。
 */
export function useTakeawayPages(
  engine: unknown,
  document: unknown,
  marks: readonly MarkSource[] | undefined,
): Map<number, number[]> {
  // 結果綁著它所屬的 (document, marks)：換文件或換摘錄時舊結果自然失效，不必在 effect 裡重設
  const [found, setFound] = useState<{ document: unknown; marks: unknown; pages: Map<number, number[]> } | null>(null)

  useEffect(() => {
    if (!document || !marks?.length || !isSearchEngine(engine)) return
    let cancelled = false
    let current: EngineSearchTask | null = null

    const run = async () => {
      const pages = new Map<number, number[]>()
      for (const m of marks) {
        for (const needle of needleLadder(m.quote)) {
          if (cancelled) return
          let out
          try {
            current = engine.searchAllPages(document, needle)
            out = await current.toPromise()
          } catch {
            if (cancelled) return
            continue
          }
          if (cancelled) return
          const first = out.results[0]
          if (out.total > 0 && first) {
            pages.set(first.pageIndex, [...(pages.get(first.pageIndex) ?? []), m.ordinal])
            break
          }
        }
      }
      if (!cancelled) setFound({ document, marks, pages })
    }

    const timer = setTimeout(() => { void run() }, START_DELAY_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
      try {
        current?.abort?.({ code: 'cancelled', message: 'unmounted' })
      } catch {
        // 引擎已結束這個任務；沒有東西要收
      }
    }
  }, [engine, document, marks])

  return found && found.document === document && found.marks === marks ? found.pages : EMPTY
}

const EMPTY: Map<number, number[]> = new Map()
