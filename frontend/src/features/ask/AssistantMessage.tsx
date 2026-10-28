import { ArrowRight, ChevronLeft, ChevronRight, CircleAlert, CornerDownRight, RefreshCw, RotateCw, ThumbsDown, ThumbsUp } from 'lucide-react'
import { Callout } from '../../components/primitives/Callout'
import { marketLabel, marketTint } from '../../lib/meta'
import { renderAnswer } from '../../lib/askMarkdown'
import { visibleAnswerView, type AnswerView, type Turn } from '../../lib/askReducer'
import { noticeDisplayText, useWebSearchPaused } from '../../lib/useWebSearch'
import { CiteChip } from './CiteChip'
import { CopyIconButton } from './CopyIconButton'
import { ThinkingSteps } from './ThinkingSteps'
import { sourceItems } from './sourceItems'
import styles from './AssistantMessage.module.css'

// 回答下方直接列出的來源卡片數（其餘收進「全部 N 則來源」）。
const MINI_SOURCES = 3

interface Props {
  turn: Turn
  /** 對話的最後一輪：延伸提問只掛在這裡（中間輪的追問會讓人以為要接在那一輪後面）。 */
  isLast?: boolean
  /** 點引用編號或來源卡片：開來源抽屜並捲到第 n 則。 */
  onCite: (n: number, view: AnswerView) => void
  /** 「全部 N 則來源」：開來源抽屜、不指定哪一則。 */
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

export function AssistantMessage({ turn, isLast = true, onCite, onOpenSources, onFeedback, onNoticeRetry, onErrorRetry, onRegenerate, onFollowup, onSetVersion, disabled = false }: Props) {
  const webPaused = useWebSearchPaused()
  if (turn.phase === 'notice') {
    // 時效題刻意不給「換個說法重新提問」：系統缺的是資料不是措辭，換說法只會讓
    // 使用者反覆改寫同一題，每一次再吃一輪完整檢索。離題題才是換個說法就有救。
    const retry = turn.noticeKind === 'time_sensitive'
      ? undefined
      : { label: '換個說法重新提問', onClick: onNoticeRetry }
    // 網搜暫停期間剝掉「可開網搜」那一句（理由見 noticeDisplayText）。
    const text = turn.noticeText == null ? '無法回答此問題' : noticeDisplayText(turn.noticeText, turn.noticeKind, webPaused)
    return <Callout variant="warning" action={retry}>{text}</Callout>
  }

  // 目前顯示版本：live（正在看最新版，versionIndex===versionCount-1）或歷史快照。
  // 歷史對話重載時 versionCount>1 但 priorVersions 尚未載入（見 TurnRow 的 pager 首次點擊觸發
  // loadVersions）；此時快照取不到，退回顯示 live 內容，避免讀取 undefined 炸掉。
  const isLive = turn.versionIndex === turn.versionCount - 1
  const view = visibleAnswerView(turn)
  const streaming = turn.phase === 'streaming'
  const items = sourceItems(view)
  const settled = (turn.phase === 'done' || turn.phase === 'stopped') && !turn.isOfftopic
  const showThinking = turn.stages.length > 0 || turn.phase === 'thinking' || turn.phase === 'streaming'

  return (
    <div className={styles.msg}>
      {showThinking && <ThinkingSteps turn={turn} referenced={view.sources.length} />}
      {/* 失敗時仍保留已串出的部分答案（reducer 有保留 turn.answer），只在下方補失敗列，
          不整段抹除使用者已讀到的內容。 */}
      {view.answer && (
        <div className={styles.body} data-streaming={streaming ? '' : undefined}>
          {renderAnswer(view.answer, view.sources.length, n => onCite(n, view), view.sources, {
            // 串流中才把文字切段：新出現的那幾段在掛載時淡入，收尾後回到純文字。
            streamChunkClass: streaming ? styles.tok : undefined,
            renderCite: (n, source, cite) => <CiteChip n={n} source={source} onCite={cite} pop={streaming} />,
          })}
        </div>
      )}
      {/* 停止標記跟著「目前顯示的版本」走：live 是停止輪（phase）或 pager 正切在
          某個被停止的舊版（view.stopped），兩者都要標——否則停止的部分答案在版本
          切換時會偽裝成完整回答。 */}
      {view.stopped && <div className={styles.stopped}><span className={styles.stoppedMark} aria-hidden="true" />已停止生成</div>}
      {turn.phase === 'error' && (
        <div className={styles.fail} role="alert">
          <CircleAlert size={18} strokeWidth={1.8} className={styles.failIcon} aria-hidden />
          <span className={styles.failText}>
            <span className={styles.failTitle}>回答沒有完成</span>
            <span className={styles.failMsg}>{turn.errorText ?? '查詢逾時或失敗'}</span>
          </span>
          <button type="button" className={styles.btn} onClick={onErrorRetry} disabled={disabled}>
            <RotateCw size={14} strokeWidth={1.8} aria-hidden />重試
          </button>
        </div>
      )}
      {settled && items.length > 0 && (
        <div className={styles.sources}>
          {items.slice(0, MINI_SOURCES).map((s, i) => (
            <button key={s.n} type="button" className={styles.mini} style={{ animationDelay: `${i * 0.04}s` }} onClick={() => onCite(s.n, view)}>
              <span className={styles.miniTop}>
                <span className={styles.num}>{s.n}</span>
                {s.kind === 'report'
                  ? <span className={styles.mkt} style={marketTint(s.market)}>{marketLabel(s.market)}</span>
                  : <span className={`${styles.mkt} ${styles.webTag}`}>網路</span>}
                <span className={s.kind === 'report' ? styles.miniDate : styles.miniHost}>{s.kind === 'report' ? s.date : s.host}</span>
              </span>
              <span className={styles.miniTitle}>{s.title}</span>
            </button>
          ))}
          <button type="button" className={styles.allSrc} onClick={() => onOpenSources(view)}>
            全部 {items.length} 則來源<ArrowRight size={14} strokeWidth={1.8} aria-hidden />
          </button>
        </div>
      )}
      {settled && (
        <div className={styles.actions}>
          {/* 讚/倒讚是切換鈕：再點一次已亮起的那顆傳 null＝取消（誤按無法收回的話，
              使用者只剩「按另一顆」這條假出口，那會把錯的評價留在 qa_log 裡）。
              切換狀態同時給 aria-pressed，否則亮起與否只有視覺上看得出來。 */}
          {isLive && view.qaId && (
            <>
              <button type="button" className={`${styles.act} ${view.feedback === 'like' ? styles.on : ''}`} onClick={() => onFeedback(view.feedback === 'like' ? null : 'like')} aria-label="讚" aria-pressed={view.feedback === 'like'}>
                <ThumbsUp size={15} strokeWidth={1.8} aria-hidden />
              </button>
              <button type="button" className={`${styles.act} ${view.feedback === 'dislike' ? styles.on : ''}`} onClick={() => onFeedback(view.feedback === 'dislike' ? null : 'dislike')} aria-label="倒讚" aria-pressed={view.feedback === 'dislike'}>
                <ThumbsDown size={15} strokeWidth={1.8} aria-hidden />
              </button>
            </>
          )}
          {view.answer && <CopyIconButton text={view.answer} label="複製回答" className={styles.act} checkClassName={styles.copied} />}
          <button type="button" className={styles.act} onClick={onRegenerate} aria-label="重新生成" title="重新生成" disabled={disabled}>
            <RefreshCw size={15} strokeWidth={1.8} aria-hidden />
          </button>
          {turn.versionCount > 1 && (
            <span className={styles.pager}>
              <button type="button" className={`${styles.act} ${styles.pagerBtn}`} disabled={turn.versionIndex === 0} aria-label="上一版" onClick={() => onSetVersion(turn.versionIndex - 1)}>
                <ChevronLeft size={14} strokeWidth={2} aria-hidden />
              </button>
              <span className={styles.pagerText}>{turn.versionIndex + 1} / {turn.versionCount}</span>
              <button type="button" className={`${styles.act} ${styles.pagerBtn}`} disabled={turn.versionIndex === turn.versionCount - 1} aria-label="下一版" onClick={() => onSetVersion(turn.versionIndex + 1)}>
                <ChevronRight size={14} strokeWidth={2} aria-hidden />
              </button>
            </span>
          )}
        </div>
      )}
      {isLast && isLive && turn.followups.length > 0 && settled && (
        <div className={styles.followups}>
          <div className={styles.fuHead}>延伸提問</div>
          {turn.followups.map((f, i) => (
            <button key={i} type="button" className={styles.fu} style={{ animationDelay: `${(i + 3) * 0.04}s` }} onClick={() => onFollowup(f)} disabled={disabled}>
              <CornerDownRight size={15} strokeWidth={1.8} className={styles.fuIcon} aria-hidden />
              <span className={styles.fuText}>{f}</span>
              <ArrowRight size={15} strokeWidth={1.8} className={styles.fuArrow} aria-hidden />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
