import type { AskEvent, ReportEvent, AskStage, ReportStage, Source, ExtSource, ConversationTurn, QaVersion } from './askSchemas'

const HTTP = /^https?:\/\//i

/** 大綱裡的一節。state 是這一節在生成流程中的下場。 */
export interface ReportSection {
  position: number
  heading: string
  state: 'pending' | 'done' | 'skipped'
}

export interface ReportState {
  status: 'idle' | 'offered' | 'generating' | 'done' | 'error'
  downloadUrl: string | null
  title: string | null
  errorText: string | null
  // 換皮重出（M9b）需要 report_id。從 downloadUrl 反解字串太脆（路徑一改就靜默失效），
  // 直接從 done 事件帶下來。
  reportId: string | null
  // ── 進度（pct/剩餘時間由 lib/reportProgress 的純函式從這三個欄位算出，不存冗餘）──
  stage: ReportStage | null
  sections: ReportSection[]
  /** 背景生成的起始時刻（ms epoch）。由 run 事件的 elapsed_ms 回推，故重整後仍正確。 */
  startedAt: number | null
  /** 背景 run 的 handle：重整後靠它接回，也是「取消生成」的對象。 */
  runId: string | null
  /** 併發滿載排隊中時的名次（null＝沒在排隊）。研報序列化，第二個人可能等十分鐘。 */
  queuePosition: number | null
}
const idleReport: ReportState = {
  status: 'idle', downloadUrl: null, title: null, errorText: null, reportId: null,
  stage: null, sections: [], startedAt: null, runId: null, queuePosition: null,
}

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
  /** 併發滿載排隊中時的名次（null＝沒在排隊）。見 web/concurrency.py 的 queued 事件。 */
  queuePosition: number | null
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
  | { type: 'report-start'; id: string; startedAt: number }
  // startedAt 由 controller 依 run 事件的 elapsed_ms 回推後傳入（reducer 保持純函式）。
  | { type: 'report-event'; id: string; event: ReportEvent; startedAt?: number }
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

function applyAsk(turn: Turn, ev: AskEvent): Turn {
  // 排隊狀態只由 queued 事件開啟，並由**任何**後續事件關閉。集中在這裡而不是逐 case
  // 清，是因為漏掉一個 case 的症狀是「答案都串出來了畫面還寫著排隊中」——一種不會
  // 報錯、只會讓人不信任介面的錯。
  if (ev.event === 'queued') return { ...turn, queuePosition: ev.data.position ?? 0 }
  const t = turn.queuePosition === null ? turn : { ...turn, queuePosition: null }
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

function markSection(sections: ReportSection[], position: number, state: 'done' | 'skipped'): ReportSection[] {
  // 依 position 標記而非累加計數：section_draft 會因 n_unknown 重生與 M8 修正一輪
  // 對同一節重送，用計數器會超過總數、進度條爬到 100% 然後倒退。
  return sections.map(s => (s.position === position ? { ...s, state } : s))
}

function applyReport(turn: Turn, ev: ReportEvent, startedAt: number | null): Turn {
  // 同 applyAsk：queued 開、任何後續事件關。研報這條還多一個理由——queued 會進重播
  // 緩衝（見 web/report_runs.py），重連時可能收到一個早已過期的排隊事件，靠後面接著
  // 重播的 status/outline 自我修正。
  if (ev.event === 'queued') {
    return { ...turn, report: { ...turn.report, status: 'generating', queuePosition: ev.data.position ?? 0 } }
  }
  const t = turn.report.queuePosition === null
    ? turn
    : { ...turn, report: { ...turn.report, queuePosition: null } }
  switch (ev.event) {
    case 'run': return { ...t, report: { ...t.report, status: 'generating', runId: ev.data.run_id, startedAt: startedAt ?? t.report.startedAt } }
    case 'status': return { ...t, report: { ...t.report, status: 'generating', stage: ev.data.stage } }
    // 空 sections＝後端退單次生成，明確要求前端放掉分母（見 app/services/report.py）。
    // 不清掉的話畫面會留著一份永遠寫不完的章節清單。
    case 'outline': return {
      ...t,
      report: {
        ...t.report,
        sections: ev.data.sections.map(s => ({ position: s.position, heading: s.heading, state: 'pending' as const })),
      },
    }
    case 'section_draft': return { ...t, report: { ...t.report, sections: markSection(t.report.sections, ev.data.position, 'done') } }
    case 'section_skipped': return { ...t, report: { ...t.report, sections: markSection(t.report.sections, ev.data.position, 'skipped') } }
    case 'sources': return t
    case 'token': return t
    case 'done': return { ...t, report: { ...t.report, status: 'done', stage: null, downloadUrl: ev.data.download_url, title: ev.data.title, errorText: null, reportId: ev.data.report_id, runId: null } }
    case 'error': return { ...t, report: { ...t.report, status: 'error', errorText: ev.data.detail, runId: null } }
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
        queuePosition: null,
      }],
    }
    case 'ask-event': return { turns: mapTurn(state.turns, action.id, t => applyAsk(t, action.event)) }
    case 'ask-end': return {
      turns: mapTurn(state.turns, action.id, t => {
        if (t.phase === 'notice' || t.phase === 'done' || t.phase === 'error' || t.phase === 'stopped') return t
        return { ...t, phase: 'error', errorText: '查詢逾時或失敗' }
      }),
    }
    case 'report-start': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, report: { ...idleReport, status: 'generating', title: t.reportTitle, startedAt: action.startedAt } })) }
    case 'report-event': return { turns: mapTurn(state.turns, action.id, t => applyReport(t, action.event, action.startedAt ?? null)) }
    case 'report-fail': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, report: { ...t.report, status: 'error', errorText: action.errorText, runId: null } })) }
    case 'report-decline': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, report: idleReport, offerReport: false })) }
    case 'report-cancel': return {
      turns: mapTurn(state.turns, action.id, t =>
        t.report.status === 'generating'
          ? { ...t, report: { ...idleReport, status: 'offered', title: t.reportTitle } }
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
          noticeText: null, versionCount: priorVersions.length + 1, queuePosition: null,
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
        priorVersions: [], versionIndex: 0, rootQaId: null, versionCount: 1, queuePosition: null,
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
    report: last
      ? { ...idleReport, status: 'done', downloadUrl: last.download_url, title: last.title, reportId: last.report_id ?? null }
      : idleReport,
    errorText: null,
    followups: item.followups,
    priorVersions: [],
    // priorVersions 尚未載入（見 AskPage pager 首次點擊觸發 loadVersions）；
    // 多版本時先假定使用者看的是最新版，index 對齊 versionCount-1，
    // 避免 pager 標籤（N/M）與實際顯示內容（永遠是 liveView）錯位。
    versionIndex: item.version_count > 1 ? item.version_count - 1 : 0,
    rootQaId: item.root_qa_id,
    versionCount: item.version_count,
    queuePosition: null,
  }
}
