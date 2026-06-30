import type { TurnState } from './conversation'

export type StepState = 'pending' | 'active' | 'done'
export interface ProcStep {
  key: string
  label: string
  state: StepState
  hidden: boolean
}

// stage → 「活躍步驟索引」（understand=0 retrieved=1 reading=2 web=3 generate=4）
const STAGE_ACTIVE: Record<string, number> = {
  understanding: 0,
  retrieved: 2,
  reading: 2,
  searching_web: 3,
  generating: 4,
}

export function thinkingLabel(ms: number | null): string | null {
  return typeof ms === 'number' && ms >= 0 ? `已思考 ${Math.max(1, Math.round(ms / 1000))} 秒` : null
}

export function deriveProcess(turn: TurnState): { steps: ProcStep[]; headLabel: string; headDone: boolean } {
  const done = turn.phase !== 'streaming'
  const active = done ? 5 : turn.stage != null ? STAGE_ACTIVE[turn.stage] ?? 0 : 0
  const st = (idx: number): StepState => (done || idx < active ? 'done' : idx === active ? 'active' : 'pending')
  const retrievedLabel = turn.sources.length ? `找到 ${turn.sources.length} 篇相關研報` : '檢索研報'
  const steps: ProcStep[] = [
    { key: 'understand', label: '理解問題', state: st(0), hidden: false },
    { key: 'retrieved', label: retrievedLabel, state: st(1), hidden: false },
    { key: 'reading', label: '閱讀重點、整理回答', state: st(2), hidden: false },
    { key: 'web', label: '搜尋網路補充', state: st(3), hidden: !turn.webUsed },
    { key: 'generate', label: '生成回答', state: st(4), hidden: false },
  ]
  return { steps, headLabel: done ? thinkingLabel(turn.thinkingMs) ?? '處理過程' : '正在思考', headDone: done }
}
