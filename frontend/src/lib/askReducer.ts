import type { AskEvent, AskStage, Source, ExtSource, ConversationTurn, NoticeKind, QaVersion } from './askSchemas'

const HTTP = /^https?:\/\//i

export interface TurnVersion {
  answer: string
  sources: Source[]
  extSources: ExtSource[]
  qaId: string | null
  thinkingMs: number | null
  stages: AskStage[]
  feedback: 'like' | 'dislike' | null
  followups: string[]
  /** 這一版是不是被中斷的部分答案。缺了它，停止列在版本 pager 裡會偽裝成完整回答。 */
  stopped: boolean
}

export interface AnswerView {
  answer: string
  sources: Source[]
  extSources: ExtSource[]
  feedback: 'like' | 'dislike' | null
  qaId: string | null
  stopped: boolean
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
  /** 固定婉拒的來源：離題 vs 時效資料不可得。null＝不是婉拒。見 askSchemas 的 noticeKind。 */
  noticeKind: NoticeKind | null
  feedback: 'like' | 'dislike' | null
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
    stopped: turn.phase === 'stopped',
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
  | { type: 'feedback'; id: string; value: 'like' | 'dislike' | null }
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

/**
 * 重生「一個 token 都還沒串出來」就失敗時，回滾 regenerate-start 的樂觀變更：
 * 把最後一個快照還原成 live、versionCount 退回。不回滾的話，error 分支不渲染
 * 版本 pager，被快照走的舊答案在畫面上完全不可達——使用者看到的是「按了重新
 * 生成，原本的答案被吃掉了」。已串出部分文字時不回滾（部分答案本身有資訊價值，
 * error 分支會照常渲染它）。
 */
function rollbackRegenIfEmpty(t: Turn): Turn {
  if (t.answer !== '' || t.priorVersions.length === 0) return t
  if (t.phase !== 'thinking' && t.phase !== 'streaming') return t
  const prior = t.priorVersions[t.priorVersions.length - 1]
  return {
    ...t,
    answer: prior.answer, sources: prior.sources, extSources: prior.extSources,
    qaId: prior.qaId, thinkingMs: prior.thinkingMs, stages: prior.stages,
    feedback: prior.feedback, followups: prior.followups,
    priorVersions: t.priorVersions.slice(0, -1),
    versionCount: t.versionCount - 1,
    versionIndex: t.priorVersions.length - 1,
  }
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
      // 後端只在整串收尾改動了內容時才帶 answer（簡繁轉換或棄稿段移除）；沒帶就
      // 維持串流累積的那份。
      // 這裡是畫面與 qa_log 收斂到同一份文字的唯一時機（見 askSchemas 的欄位註解）。
      answer: ev.data.answer ?? t.answer,
      qaId: ev.data.qa_id ?? t.qaId,
      noticeKind: ev.data.notice_kind ?? t.noticeKind,
      rootQaId: ev.data.root_qa_id ?? t.rootQaId,
      versionCount: ev.data.version_count ?? t.versionCount,
      // done 時剛完成的答案即最新版，versionIndex 對齊最新——修正「重載多版本後直接重生」時
      // 伺服器權威 version_count 晚到、樂觀 versionIndex 未同步導致 isLive 誤 false 而隱藏回饋/追問鈕。
      versionIndex: (ev.data.version_count ?? t.versionCount) - 1,
    }
    case 'error': {
      const rolled = rollbackRegenIfEmpty(t)
      return { ...rolled, phase: 'error', errorText: ev.data.detail }
    }
  }
}

export function askReducer(state: AskState, action: AskAction): AskState {
  switch (action.type) {
    case 'submit': return {
      turns: [...state.turns, {
        id: action.id, question: action.question, phase: 'thinking', stages: ['understanding'],
        webUsed: false, retrievedCount: null, answer: '', thinkingMs: null, startedAt: action.startedAt,
        sources: [], extSources: [], qaId: null, isOfftopic: false, noticeText: null, noticeKind: null,
        feedback: null, errorText: null,
        followups: [], priorVersions: [], versionIndex: 0, rootQaId: null, versionCount: 1,
        queuePosition: null,
      }],
    }
    case 'ask-event': return { turns: mapTurn(state.turns, action.id, t => applyAsk(t, action.event)) }
    case 'ask-end': return {
      turns: mapTurn(state.turns, action.id, t => {
        // queuePosition 一併清掉：排隊中就失敗（伺服器重啟、連線斷）時，留著會讓
        // 錯誤輪的思考卡標籤仍寫「排隊中…」——與 applyAsk 的「任何後續事件關閉排隊」
        // 同一條規則，只是這裡的後續事件是終局。
        if (t.phase === 'notice' || t.phase === 'done' || t.phase === 'error' || t.phase === 'stopped') return t
        return { ...rollbackRegenIfEmpty(t), phase: 'error', errorText: '查詢逾時或失敗', queuePosition: null }
      }),
    }
    case 'feedback': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, feedback: action.value })) }
    case 'load': return { turns: action.turns }
    case 'reset': return { turns: [] }
    case 'ask-stop': return {
      // queuePosition 清掉的理由同 ask-end：排隊中按停止，留著會讓「已停止」的輪
      // 同時顯示「排隊中…」與排隊說明，兩個狀態互相矛盾。
      turns: mapTurn(state.turns, action.id, t => ({ ...t, phase: 'stopped', qaId: action.qaId ?? t.qaId, queuePosition: null })),
    }
    case 'followups': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, followups: action.data })) }
    case 'regenerate-start': return {
      turns: mapTurn(state.turns, action.id, t => {
        const snapshot: TurnVersion = {
          answer: t.answer, sources: t.sources, extSources: t.extSources, qaId: t.qaId,
          thinkingMs: t.thinkingMs, stages: t.stages, feedback: t.feedback, followups: t.followups,
          stopped: t.phase === 'stopped',
        }
        const priorVersions = [...t.priorVersions, snapshot]
        return {
          ...t, priorVersions, versionIndex: priorVersions.length,
          phase: 'thinking', stages: ['understanding'], answer: '', thinkingMs: null,
          sources: [], extSources: [], followups: [], errorText: null, isOfftopic: false,
          noticeText: null, noticeKind: null, versionCount: priorVersions.length + 1, queuePosition: null,
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
        isOfftopic: false, noticeText: null, noticeKind: null,
        feedback: null, errorText: null, followups: [],
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
          stopped: v.stopped,
        }))
        return { ...t, priorVersions: prior, versionIndex: prior.length, versionCount: action.versions.length }
      }),
    }
  }
}

export function turnFromHistory(item: ConversationTurn): Turn {
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
    noticeKind: item.notice_kind ?? null,
    feedback: item.feedback,
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
