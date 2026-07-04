import type { AskEvent, ReportEvent, AskStage, Source, ExtSource, ConversationTurn } from './askSchemas'
import { reportProgress } from './reportProgress'

const HTTP = /^https?:\/\//i

export interface ReportState {
  status: 'idle' | 'offered' | 'generating' | 'done' | 'error'
  pct: number
  stageText: string
  downloadUrl: string | null
  title: string | null
  errorText: string | null
}
const idleReport: ReportState = { status: 'idle', pct: 0, stageText: '', downloadUrl: null, title: null, errorText: null }

export interface Turn {
  id: string
  question: string
  phase: 'thinking' | 'streaming' | 'done' | 'notice' | 'error'
  stages: AskStage[]
  webUsed: boolean
  retrievedCount: number | null
  answer: string
  thinkingMs: number | null
  startedAt: number
  sources: Source[]
  extSources: ExtSource[]
  qaId: string | null
  isOfftopic: boolean
  noticeText: string | null
  offerReport: boolean
  reportTitle: string | null
  feedback: 'like' | 'dislike' | null
  report: ReportState
  errorText: string | null
}

export interface AskState { turns: Turn[] }
export const initialAskState: AskState = { turns: [] }

export type AskAction =
  | { type: 'submit'; id: string; question: string; startedAt: number }
  | { type: 'ask-event'; id: string; event: AskEvent }
  | { type: 'ask-end'; id: string }
  | { type: 'report-start'; id: string }
  | { type: 'report-event'; id: string; event: ReportEvent }
  | { type: 'report-fail'; id: string; errorText: string }
  | { type: 'report-decline'; id: string }
  | { type: 'report-cancel'; id: string }
  | { type: 'feedback'; id: string; value: 'like' | 'dislike' }
  | { type: 'load'; turns: Turn[] }
  | { type: 'reset' }

function mapTurn(turns: Turn[], id: string, fn: (t: Turn) => Turn): Turn[] {
  return turns.map(t => (t.id === id ? fn(t) : t))
}

function applyAsk(t: Turn, ev: AskEvent): Turn {
  switch (ev.event) {
    case 'status': {
      const stage = ev.data.stage
      const stages = t.stages.includes(stage) ? t.stages : [...t.stages, stage]
      return {
        ...t, stages,
        webUsed: t.webUsed || stage === 'searching_web',
        retrievedCount: stage === 'retrieved' && ev.data.count != null ? ev.data.count : t.retrievedCount,
        phase: stage === 'generating' && t.phase === 'thinking' ? 'streaming' : t.phase,
        thinkingMs: ev.data.thinking_ms ?? t.thinkingMs,
      }
    }
    case 'sources': return { ...t, sources: ev.data }
    case 'ext_sources': return { ...t, extSources: ev.data.filter(e => HTTP.test(e.url)) }
    case 'token': return { ...t, answer: t.answer + ev.data, phase: t.isOfftopic ? 'notice' : 'streaming' }
    case 'notice': return { ...t, phase: 'notice', isOfftopic: true, noticeText: ev.data }
    case 'done': return {
      ...t,
      phase: t.isOfftopic ? 'notice' : 'done',
      qaId: ev.data.qa_id ?? null,
      offerReport: ev.data.offer_report ?? false,
      reportTitle: ev.data.report_title ?? null,
      report: ev.data.offer_report ? { ...t.report, status: 'offered', title: ev.data.report_title ?? null } : t.report,
    }
  }
}

function applyReport(t: Turn, ev: ReportEvent): Turn {
  switch (ev.event) {
    case 'status': { const { pct, text } = reportProgress(ev.data.stage); return { ...t, report: { ...t.report, status: 'generating', pct, stageText: text } } }
    case 'sources': return t
    case 'token': return t
    case 'done': return { ...t, report: { ...t.report, status: 'done', pct: 100, downloadUrl: ev.data.download_url, title: ev.data.title, errorText: null } }
    case 'error': return { ...t, report: { ...t.report, status: 'error', errorText: ev.data.detail } }
  }
}

export function askReducer(state: AskState, action: AskAction): AskState {
  switch (action.type) {
    case 'submit': return {
      turns: [...state.turns, {
        id: action.id, question: action.question, phase: 'thinking', stages: ['understanding'],
        webUsed: false, retrievedCount: null, answer: '', thinkingMs: null, startedAt: action.startedAt,
        sources: [], extSources: [], qaId: null, isOfftopic: false, noticeText: null,
        offerReport: false, reportTitle: null, feedback: null, report: idleReport, errorText: null,
      }],
    }
    case 'ask-event': return { turns: mapTurn(state.turns, action.id, t => applyAsk(t, action.event)) }
    case 'ask-end': return {
      turns: mapTurn(state.turns, action.id, t => {
        if (t.phase === 'notice' || t.phase === 'done' || t.phase === 'error') return t
        return t.answer === '' ? { ...t, phase: 'error', errorText: '查詢逾時或失敗' } : { ...t, phase: 'done' }
      }),
    }
    case 'report-start': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, report: { status: 'generating', pct: 0, stageText: '準備生成研報…', downloadUrl: null, title: t.reportTitle, errorText: null } })) }
    case 'report-event': return { turns: mapTurn(state.turns, action.id, t => applyReport(t, action.event)) }
    case 'report-fail': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, report: { ...t.report, status: 'error', errorText: action.errorText } })) }
    case 'report-decline': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, report: idleReport, offerReport: false })) }
    case 'report-cancel': return {
      turns: mapTurn(state.turns, action.id, t =>
        t.report.status === 'generating'
          ? { ...t, report: { status: 'offered', pct: 0, stageText: '', downloadUrl: null, title: t.reportTitle, errorText: null } }
          : t),
    }
    case 'feedback': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, feedback: action.value })) }
    case 'load': return { turns: action.turns }
    case 'reset': return { turns: [] }
  }
}

export function turnFromHistory(item: ConversationTurn): Turn {
  const last = item.reports.length ? item.reports[item.reports.length - 1] : null
  return {
    id: item.id,
    question: item.question,
    phase: item.is_offtopic ? 'notice' : 'done',
    stages: [],
    webUsed: false,
    retrievedCount: null,
    answer: item.answer,
    thinkingMs: item.thinking_ms,
    startedAt: 0,
    sources: item.sources,
    extSources: item.ext_sources.filter(e => HTTP.test(e.url)),
    qaId: item.is_offtopic ? null : item.id,
    isOfftopic: item.is_offtopic,
    noticeText: item.is_offtopic ? item.answer : null,
    offerReport: false,
    reportTitle: null,
    feedback: item.feedback,
    report: last ? { status: 'done', pct: 100, stageText: '', downloadUrl: last.download_url, title: last.title, errorText: null } : idleReport,
    errorText: null,
  }
}
