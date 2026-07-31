import type { AskStage } from './askSchemas'

export type StepState = 'done' | 'active' | 'pending'
export interface ThinkStep {
  key: AskStage
  name: string
  state: StepState
}

// 順序＝實際發生順序。retrieved 排在 evaluating 之前：後端已把 retrieved 的推進提前到
// 檢索開始（rerank 是整條路徑最長的靜默窗），而 agentic 的 evaluating 必然發生在第一輪
// 檢索**之後**。兩者對調前，這一格會在檢索途中亮 40 秒都不動。
const ORDER: { key: AskStage; name: string }[] = [
  { key: 'understanding', name: '理解問題' },
  { key: 'retrieved', name: '檢索研報' },
  { key: 'evaluating', name: '評估補查' },
  { key: 'reading', name: '閱讀整理' },
  { key: 'searching_web', name: '網路補充' },
  { key: 'generating', name: '生成回答' },
]

export function stagesToSteps(reached: AskStage[], webUsed: boolean): ThinkStep[] {
  // evaluating 與 searching_web 皆為條件性步驟：未實際發生的流程不顯示假步驟
  const visible = ORDER.filter(
    s => (s.key !== 'searching_web' || webUsed) && (s.key !== 'evaluating' || reached.includes('evaluating'))
  )
  // 取「抵達過的最遠一步」而非最後一筆：retrieved 會送兩次（檢索前推進、檢索後補
  // count），第二筆落在 evaluating 之後，用最後一筆會讓進度倒退一格。findIndex 對
  // 不可見步驟回 -1，Math.max 自然忽略。
  const lastIdx = reached.reduce(
    (mx, key) => Math.max(mx, visible.findIndex(s => s.key === key)),
    -1
  )
  return visible.map((s, i) => ({
    key: s.key,
    name: s.name,
    state: lastIdx === -1 ? 'pending' : i < lastIdx ? 'done' : i === lastIdx ? 'active' : 'pending',
  }))
}
