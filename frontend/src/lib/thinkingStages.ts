import type { AskStage } from './askSchemas'

export type StepState = 'done' | 'active' | 'pending'
export interface ThinkStep {
  key: AskStage
  name: string
  state: StepState
}

const ORDER: { key: AskStage; name: string }[] = [
  { key: 'understanding', name: '理解問題' },
  { key: 'retrieved', name: '檢索研報' },
  { key: 'reading', name: '閱讀整理' },
  { key: 'searching_web', name: '網路補充' },
  { key: 'generating', name: '生成回答' },
]

export function stagesToSteps(reached: AskStage[], webUsed: boolean): ThinkStep[] {
  const visible = ORDER.filter(s => s.key !== 'searching_web' || webUsed)
  const last = reached.length ? reached[reached.length - 1] : null
  const lastIdx = last ? visible.findIndex(s => s.key === last) : -1
  return visible.map((s, i) => ({
    key: s.key,
    name: s.name,
    state: lastIdx === -1 ? 'pending' : i < lastIdx ? 'done' : i === lastIdx ? 'active' : 'pending',
  }))
}
