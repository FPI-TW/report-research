import { Callout } from '../../components/primitives/Callout'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import { CopyButton } from '../../components/animate-ui/components/buttons/copy'
import { answerMarkdown } from '../../lib/exportMarkdown'
import { ThinkingSteps } from './ThinkingSteps'
import { renderAnswer } from '../../lib/askMarkdown'
import { visibleAnswerView, type AnswerView, type Turn } from '../../lib/askReducer'
import styles from './AssistantMessage.module.css'

interface Props {
  turn: Turn
  onCite: (n: number, view: AnswerView) => void
  onOpenSources: (view: AnswerView) => void
  /** 傳的是「按下之後應該變成什麼」：再點一次已亮起的那顆會傳 null（取消）。 */
  onFeedback: (v: 'like' | 'dislike' | null) => void
  onNoticeRetry: () => void
  onErrorRetry: () => void
  onRegenerate: () => void
  onFollowup: (t: string) => void
  onSetVersion: (i: number) => void
  disabled?: boolean
}

export function AssistantMessage({ turn, onCite, onOpenSources, onFeedback, onNoticeRetry, onErrorRetry, onRegenerate, onFollowup, onSetVersion, disabled = false }: Props) {
  if (turn.phase === 'notice') {
    // 時效題刻意不給「換個說法重新提問」：系統缺的是資料不是措辭，換說法只會讓
    // 使用者反覆改寫同一題，每一次再吃一輪完整檢索。離題題才是換個說法就有救。
    const retry = turn.noticeKind === 'time_sensitive'
      ? undefined
      : { label: '換個說法重新提問', onClick: onNoticeRetry }
    return <Callout variant="warning" action={retry}>{turn.noticeText ?? '無法回答此問題'}</Callout>
  }
  if (turn.phase === 'error') {
    // 串流中斷/發生錯誤時仍保留已串出的部分答案（reducer 有保留 turn.answer），僅在下方補錯誤提示，
    // 不整段抹除使用者已讀到的內容。無部分答案時退化為單純錯誤 Callout。
    return (
      <div className={styles.msg}>
        {turn.answer && (
          <div className={styles.body}>
            {renderAnswer(
              turn.answer,
              turn.sources.length,
              n => onCite(n, visibleAnswerView(turn)),
              turn.sources,
            )}
          </div>
        )}
        <Callout variant="error" action={disabled ? undefined : { label: '重試', onClick: onErrorRetry }}>{turn.errorText ?? '查詢逾時或失敗'}</Callout>
        <div className={styles.actions}>
          <Pressable className={styles.act} onClick={onRegenerate} aria-label="重新生成" title="重新生成" disabled={disabled}><Icon name="refresh" size={15} /></Pressable>
        </div>
      </div>
    )
  }

  // 目前顯示版本：live（正在看最新版，versionIndex===versionCount-1）或歷史快照。
  // 歷史對話重載時 versionCount>1 但 priorVersions 尚未載入（見 AskPage pager 首次點擊觸發
  // loadVersions）；此時快照取不到，退回顯示 live 內容，避免讀取 undefined 炸掉。
  const isLive = turn.versionIndex === turn.versionCount - 1
  const view = visibleAnswerView(turn)

  const refCount = view.sources.length + view.extSources.length
  const showActions = (turn.phase === 'done' || turn.phase === 'stopped') && !turn.isOfftopic

  return (
    <div className={styles.msg}>
      {(turn.stages.length > 0 || turn.phase === 'thinking' || turn.phase === 'streaming') && <ThinkingSteps turn={turn} />}
      {view.answer && (
        <div className={styles.body} data-streaming={turn.phase === 'streaming' ? '' : undefined}>
          {renderAnswer(view.answer, view.sources.length, n => onCite(n, view), view.sources)}
        </div>
      )}
      {/* 停止標記跟著「目前顯示的版本」走：live 是停止輪（phase）或 pager 正切在
          某個被停止的舊版（view.stopped），兩者都要標——否則停止的部分答案在版本
          切換時會偽裝成完整回答。必須是 block：思考卡是 inline-block，行內元素會
          黏到卡片右側漂著（2026-08-03 實際回報的版面缺陷）。 */}
      {view.stopped && <div className={styles.stopped}>已停止生成</div>}
      {turn.versionCount > 1 && (
        <div className={styles.pager}>
          <button
            type="button"
            className={styles.pagerBtn}
            disabled={turn.versionIndex === 0}
            aria-label="上一版"
            onClick={() => onSetVersion(turn.versionIndex - 1)}
          >
            ‹
          </button>
          <span className={styles.pagerText}>{turn.versionIndex + 1}/{turn.versionCount}</span>
          <button
            type="button"
            className={styles.pagerBtn}
            disabled={turn.versionIndex === turn.versionCount - 1}
            aria-label="下一版"
            onClick={() => onSetVersion(turn.versionIndex + 1)}
          >
            ›
          </button>
        </div>
      )}
      {showActions && (
        <div className={styles.actions}>
          {/* 讚/倒讚是切換鈕：再點一次已亮起的那顆傳 null＝取消（誤按無法收回的話，
              使用者只剩「按另一顆」這條假出口，那會把錯的評價留在 qa_log 裡）。
              切換狀態同時給 aria-pressed，否則亮起與否只有視覺上看得出來。 */}
          {isLive && view.qaId && (
            <>
              <Pressable className={`${styles.act} ${view.feedback === 'like' ? styles.on : ''}`} onClick={() => onFeedback(view.feedback === 'like' ? null : 'like')} aria-label="讚" aria-pressed={view.feedback === 'like'}><Icon name="thumbUp" size={15} /></Pressable>
              <Pressable className={`${styles.act} ${view.feedback === 'dislike' ? styles.on : ''}`} onClick={() => onFeedback(view.feedback === 'dislike' ? null : 'dislike')} aria-label="倒讚" aria-pressed={view.feedback === 'dislike'}><Icon name="thumbDown" size={15} /></Pressable>
            </>
          )}
          {/* 複製的是本文＋本文引用到的來源清單：只帶本文的話，貼出去之後 [1]、[2] 沒有
              任何意義（見 lib/exportMarkdown.ts）。 */}
          {/* 四顆動作鈕都是純圖示且等大：size="xs" 只是讓 animate-ui 自帶的方框接近目標值，
              真正釘死尺寸與圓角的是 .act（見 CSS 註解）。 */}
          <CopyButton content={answerMarkdown(view)} variant="ghost" size="xs" className={styles.act} hoverScale={1.02} tapScale={0.94} aria-label="複製回答" title="複製回答" />
          <Pressable className={styles.act} onClick={onRegenerate} aria-label="重新生成" title="重新生成" disabled={disabled}><Icon name="refresh" size={15} /></Pressable>
          {refCount > 0 && (
            <>
              <span className={styles.divider} />
              <Pressable className={styles.srcBtn} onClick={() => onOpenSources(view)}>資料來源 {refCount}</Pressable>
            </>
          )}
        </div>
      )}
      {isLive && turn.followups.length > 0 && (turn.phase === 'done' || turn.phase === 'stopped') && (
        <div className={styles.followups}>
          {turn.followups.map((f, i) => (
            <button key={i} type="button" className={styles.chip} onClick={() => onFollowup(f)} disabled={disabled}>{f}</button>
          ))}
        </div>
      )}
    </div>
  )
}
