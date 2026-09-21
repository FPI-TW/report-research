import { memo } from 'react'
import { Reveal } from '../../components/primitives/Reveal'
import type { AnswerView, Turn } from '../../lib/askReducer'
import { AssistantMessage } from './AssistantMessage'
import { UserMessage } from './UserMessage'

/**
 * 一輪問答要用到的動作。**每一個都必須是穩定參考**（useCallback／setState），
 * 否則下面的 memo 形同虛設——`useAskController` 回傳的物件本身每次 render 都是新的，
 * 所以這裡收的是它裡面的個別函式，不是整個 controller。
 */
export interface TurnActions {
  editResubmit: (turnId: string, qaId: string | null, question: string) => void
  regenerate: (turnId: string, qaId: string | null, question: string) => void
  setFeedback: (turnId: string, qaId: string, value: 'like' | 'dislike' | null) => void
  setVersion: (turnId: string, index: number) => void
  loadVersions: (turnId: string, rootId: string) => Promise<unknown>
  submit: (question: string) => void
  setDraft: (text: string) => void
  openSources: (view: AnswerView) => void
  toggleSources: (view: AnswerView) => void
}

interface Props {
  turn: Turn
  busy: boolean
  actions: TurnActions
}

/**
 * 單一輪（提問＋回答）。
 *
 * 存在的理由是 memo：串流期間每個 token 都讓 `turns` 換一個新陣列，先前 AskPage 直接在
 * map 裡用行內箭頭函式渲染，於是**所有已完成的輪次**跟著每個 token 重 render，而
 * AssistantMessage 每次都會把整份答案重新解析成 markdown——成本是「輪數 × 答案長度」每 token。
 * reducer 只替換正在串流的那一輪（其餘輪次維持同一個物件參考），所以只要 props 穩定，
 * memo 就能讓已完成的輪次完全不動。
 */
export const TurnRow = memo(function TurnRow({ turn: t, busy, actions: a }: Props) {
  return (
    <Reveal>
      <UserMessage text={t.question} onEdit={q => a.editResubmit(t.id, t.qaId, q)} disabled={busy} />
      <AssistantMessage
        turn={t}
        onCite={(_n, view) => a.openSources(view)}
        onOpenSources={a.toggleSources}
        onFeedback={v => t.qaId && a.setFeedback(t.id, t.qaId, v)}
        onNoticeRetry={() => a.setDraft(t.question)}
        onErrorRetry={() => a.submit(t.question)}
        onRegenerate={() => a.regenerate(t.id, t.qaId, t.question)}
        onFollowup={a.submit}
        disabled={busy}
        onSetVersion={i => {
          // priorVersions「不完整」（而非只有「全空」）就先補載：歷史多版本輪
          // 直接重生後，本地只有剛快照的那一版，中間版本是洞——洞的索引會
          // fallback 到 live，pager 顯示的版號與內容對不上。
          if (t.rootQaId && t.versionCount > 1 && t.priorVersions.length < t.versionCount - 1) {
            void a.loadVersions(t.id, t.rootQaId).then(loaded => {
              if (loaded) a.setVersion(t.id, i)
            })
          } else {
            a.setVersion(t.id, i)
          }
        }}
      />
    </Reveal>
  )
})
