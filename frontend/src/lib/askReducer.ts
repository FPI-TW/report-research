import type { AskEvent, ReportEvent, AskStage, Source, ExtSource, ConversationTurn, QaVersion } from './askSchemas'
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

export interface TurnVersion {
  answer: string
  sources: Source[]
  extSources: ExtSource[]
  qaId: string | null
  thinkingMs: number | null
  stages: AskStage[]
  feedback: 'like' | 'dislike' | null
  followups: string[]
}

export interface AnswerView {
  answer: string
  sources: Source[]
  extSources: ExtSource[]
  feedback: 'like' | 'dislike' | null
  qaId: string | null
}

export interface Turn {
  id: string
  question: string
  phase: 'thinking' | 'streaming' | 'done' | 'notice' | 'error' | 'stopped'
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
  followups: string[]
  priorVersions: TurnVersion[]
  versionIndex: number
  rootQaId: string | null
  versionCount: number
}

export function visibleAnswerView(turn: Turn): AnswerView {
  const live: AnswerView = {
    answer: turn.answer,
    sources: turn.sources,
    extSources: turn.extSources,
    feedback: turn.feedback,
    qaId: turn.qaId,
  }
  return turn.versionIndex === turn.versionCount - 1
    ? live
    : turn.priorVersions[turn.versionIndex] ?? live
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
  | { type: 'ask-stop'; id: string; qaId: string | null }
  | { type: 'regenerate-start'; id: string }
  | { type: 'followups'; id: string; data: string[] }
  | { type: 'set-version'; id: string; index: number }
  | { type: 'truncate-after'; id: string }
  | { type: 'submit-edit'; id: string; question: string }
  | { type: 'load-versions'; id: string; versions: QaVersion[] }

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
    case 'followups': return t  // followups 由 controller 派送專屬 action 處理；此處為型別窮盡的 no-op
    case 'done': return {
      ...t,
      phase: t.isOfftopic ? 'notice' : 'done',
      qaId: ev.data.qa_id ?? t.qaId,
      offerReport: ev.data.offer_report ?? false,
      reportTitle: ev.data.report_title ?? null,
      rootQaId: ev.data.root_qa_id ?? t.rootQaId,
      versionCount: ev.data.version_count ?? t.versionCount,
      // done 時剛完成的答案即最新版，versionIndex 對齊最新——修正「重載多版本後直接重生」時
      // 伺服器權威 version_count 晚到、樂觀 versionIndex 未同步導致 isLive 誤 false 而隱藏回饋/追問鈕。
      versionIndex: (ev.data.version_count ?? t.versionCount) - 1,
      report: ev.data.offer_report ? { ...t.report, status: 'offered', title: ev.data.report_title ?? null } : t.report,
    }
    case 'error': return { ...t, phase: 'error', errorText: ev.data.detail }
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
        followups: [], priorVersions: [], versionIndex: 0, rootQaId: null, versionCount: 1,
      }],
    }
    case 'ask-event': return { turns: mapTurn(state.turns, action.id, t => applyAsk(t, action.event)) }
    case 'ask-end': return {
      turns: mapTurn(state.turns, action.id, t => {
        if (t.phase === 'notice' || t.phase === 'done' || t.phase === 'error' || t.phase === 'stopped') return t
        return { ...t, phase: 'error', errorText: '查詢逾時或失敗' }
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
    case 'ask-stop': return {
      turns: mapTurn(state.turns, action.id, t => ({ ...t, phase: 'stopped', qaId: action.qaId ?? t.qaId })),
    }
    case 'followups': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, followups: action.data })) }
    case 'regenerate-start': return {
      turns: mapTurn(state.turns, action.id, t => {
        const snapshot: TurnVersion = {
          answer: t.answer, sources: t.sources, extSources: t.extSources, qaId: t.qaId,
          thinkingMs: t.thinkingMs, stages: t.stages, feedback: t.feedback, followups: t.followups,
        }
        const priorVersions = [...t.priorVersions, snapshot]
        return {
          ...t, priorVersions, versionIndex: priorVersions.length,
          phase: 'thinking', stages: ['understanding'], answer: '', thinkingMs: null,
          sources: [], extSources: [], followups: [], errorText: null, isOfftopic: false,
          noticeText: null, versionCount: priorVersions.length + 1,
        }
      }),
    }
    case 'set-version': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, versionIndex: action.index })) }
    case 'truncate-after': {
      const idx = state.turns.findIndex(t => t.id === action.id)
      return idx < 0 ? state : { turns: state.turns.slice(0, idx + 1) }
    }
    case 'submit-edit': return {
      turns: mapTurn(state.turns, action.id, t => ({
        ...t, question: action.question, phase: 'thinking', stages: ['understanding'],
        answer: '', thinkingMs: null, sources: [], extSources: [], qaId: null, retrievedCount: null,
        isOfftopic: false, noticeText: null, offerReport: false, reportTitle: null,
        feedback: null, report: idleReport, errorText: null, followups: [],
        priorVersions: [], versionIndex: 0, rootQaId: null, versionCount: 1,
      })),
    }
    case 'load-versions': return {
      turns: mapTurn(state.turns, action.id, t => {
        if (action.versions.length === 0) return t
        // 後端回全版本（由舊到新，末項=現用）；末項即目前顯示，其餘進 priorVersions
        const prior: TurnVersion[] = action.versions.slice(0, -1).map(v => ({
          answer: v.answer, sources: v.sources, extSources: v.ext_sources, qaId: v.qa_id,
          thinkingMs: v.thinking_ms, stages: v.stages, feedback: v.feedback, followups: [],
        }))
        return { ...t, priorVersions: prior, versionIndex: prior.length, versionCount: action.versions.length }
      }),
    }
  }
}

export function turnFromHistory(item: ConversationTurn): Turn {
  const last = item.reports.length ? item.reports[item.reports.length - 1] : null
  return {
    id: item.id,
    question: item.question,
    phase: item.is_offtopic ? 'notice' : (item.stopped ? 'stopped' : 'done'),
    stages: item.stages,
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
    followups: item.followups,
    priorVersions: [],
    // priorVersions 尚未載入（見 AskPage pager 首次點擊觸發 loadVersions）；
    // 多版本時先假定使用者看的是最新版，index 對齊 versionCount-1，
    // 避免 pager 標籤（N/M）與實際顯示內容（永遠是 liveView）錯位。
    versionIndex: item.version_count > 1 ? item.version_count - 1 : 0,
    rootQaId: item.root_qa_id,
    versionCount: item.version_count,
  }
}
