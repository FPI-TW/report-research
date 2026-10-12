import type { Progress } from '../../../../lib/progressSchema'

/**
 * 管線分頁頁首的結論。**只用既有判準，不發明門檻**：
 *
 *   bad   近 24 小時有排程失敗（`unit_failures.count_24h > 0`）。舊監控頁排程健康卡的紅點條件，
 *         刻意是時間窗計數而不是累計數：`unit_failures.log` append-only、沒有已讀游標，
 *         累計數當條件等於上線第一天就永遠亮著。sync 殼與 freshness 的失敗都寫進這份紀錄。
 *   warn  忠實度查核有 fail-open（`evaluation.qa.degraded > 0`）：judge 異常時寫 degraded、分數留空，
 *         是「查核還在跑但什麼都沒查到」——最像一切正常的故障樣態。
 *
 * 待複核筆數（忠實度低於門檻、抽取 needs_review）**刻意不進結論**：它們常態非零，算進去燈號會
 * 永遠亮著，兩週內就被當成背景噪音。頁首另列一行中性的「待人看」，連到待複核頁。
 *
 * 同步本身沒有「失敗」狀態可判：`sync.status` 只有 done／running／unknown（由 log 的最後一個標記行
 * 推得），失敗會以 unit 失敗的形式出現在上面那條。
 */
export type AttentionLevel = 'bad' | 'warn'
export type AttentionTarget = 'schedule' | 'quality'
export interface AttentionItem {
  level: AttentionLevel
  text: string
  target: AttentionTarget
}

/** 頁內錨點：結論列的「看排程與執行／看品質」跳到對應的卡。 */
export const SECTION_ID: Record<AttentionTarget, string> = {
  schedule: 'pipeline-schedule',
  quality: 'pipeline-quality',
}

export function pipelineAttention(p: Progress): AttentionItem[] {
  const items: AttentionItem[] = []
  const f = p.unit_failures
  if (f && f.count_24h > 0) {
    const latest = f.latest ? `，最近一次 ${fmtMinute(f.latest)}` : ''
    items.push({ level: 'bad', text: `近 24 小時有 ${f.count_24h} 次排程失敗${latest}`, target: 'schedule' })
  }
  const qa = p.evaluation?.qa
  if (qa && qa.degraded > 0) {
    items.push({
      level: 'warn',
      text: `問答忠實度有 ${qa.degraded} 筆 fail-open（judge 異常，該筆實際未被查核）`,
      target: 'quality',
    })
  }
  return items
}

/** 結論等級：有 bad 就是 bad，否則有 warn 就是 warn，都沒有是 ok。 */
export function attentionLevel(items: AttentionItem[]): AttentionLevel | 'ok' {
  if (items.some(i => i.level === 'bad')) return 'bad'
  return items.length > 0 ? 'warn' : 'ok'
}

/**
 * 時間戳只顯示到分。後端給的是 ISO-8601（`date -Iseconds`，含時區偏移）或
 * `YYYY-MM-DD HH:MM:SS`；秒沒有意義。認不得的格式原樣顯示，不吞掉。
 */
export function fmtMinute(ts: string | null | undefined): string {
  if (!ts) return '—'
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(ts)
  return m ? `${m[1]} ${m[2]}` : ts
}
