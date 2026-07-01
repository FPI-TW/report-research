import type { ExtSource, SourceItem } from './sseEvents'
import type { HistoryItem, ReportSummary } from '../schemas'

const HTTP = /^https?:\/\//i

export type TurnPhase = 'streaming' | 'done' | 'notice' | 'error'

export interface TurnState {
  id: string
  q: string
  answer: string
  sources: SourceItem[]
  extSources: ExtSource[]
  qaId: string | null
  thinkingMs: number | null
  feedback: 'like' | 'dislike' | null
  phase: TurnPhase
  notice: string | null
  errorMsg: string | null
  offerReport: boolean
  reportTitle: string | null
  stage: string | null
  webUsed: boolean
  reports?: ReportSummary[]
}

export function buildAskBody(
  question: string,
  conversationId: string | null,
): { question: string; conversation_id?: string } {
  return conversationId ? { question, conversation_id: conversationId } : { question }
}

export function emptyTurn(id: string, q: string): TurnState {
  return {
    id,
    q,
    answer: '',
    sources: [],
    extSources: [],
    qaId: null,
    thinkingMs: null,
    feedback: null,
    phase: 'streaming',
    notice: null,
    errorMsg: null,
    offerReport: false,
    reportTitle: null,
    stage: null,
    webUsed: false,
  }
}

export function historyToTurn(item: HistoryItem, id: string): TurnState {
  const offtopic = Boolean(item.is_offtopic)
  const fb = item.feedback === 'like' || item.feedback === 'dislike' ? item.feedback : null
  return {
    id,
    q: item.question,
    answer: item.answer ?? '',
    sources: (item.sources ?? []) as SourceItem[],
    extSources: (item.ext_sources ?? []).filter((s) => HTTP.test(s.url)) as ExtSource[],
    qaId: item.id,
    thinkingMs: typeof item.thinking_ms === 'number' ? item.thinking_ms : null,
    feedback: fb,
    phase: offtopic ? 'notice' : 'done',
    notice: offtopic ? item.answer ?? '' : null,
    errorMsg: null,
    offerReport: false,
    reportTitle: null,
    stage: null,
    webUsed: (item.ext_sources ?? []).length > 0,
    reports: item.reports ?? undefined,
  }
}
