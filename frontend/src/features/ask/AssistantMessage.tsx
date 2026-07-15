import { useEffect, useRef, useState } from 'react'
import { Callout } from '../../components/primitives/Callout'
import { Icon } from '../../components/primitives/Icon'
import { ThinkingSteps } from './ThinkingSteps'
import { renderAnswer } from '../../lib/askMarkdown'
import { visibleAnswerView, type AnswerView, type Turn } from '../../lib/askReducer'
import styles from './AssistantMessage.module.css'

interface Props {
  turn: Turn
  onCite: (n: number, view: AnswerView) => void
  onOpenSources: (view: AnswerView) => void
  onFeedback: (v: 'like' | 'dislike') => void
  onNoticeRetry: () => void
  onErrorRetry: () => void
  onRegenerate: () => void
  onFollowup: (t: string) => void
  onSetVersion: (i: number) => void
  disabled?: boolean
}

export function AssistantMessage({ turn, onCite, onOpenSources, onFeedback, onNoticeRetry, onErrorRetry, onRegenerate, onFollowup, onSetVersion, disabled = false }: Props) {
  const [copied, setCopied] = useState(false)
  const revertRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // 卸載時清掉「已複製」回復計時器，避免對已卸載元件 setState。
  useEffect(() => () => { if (revertRef.current) clearTimeout(revertRef.current) }, [])

  if (turn.phase === 'notice') {
    return <Callout variant="warning" action={{ label: '換個說法重新提問', onClick: onNoticeRetry }}>{turn.noticeText ?? '無法回答此問題'}</Callout>
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
          <button type="button" className={styles.act} onClick={onRegenerate} aria-label="重新生成" title="重新生成" disabled={disabled}>重新生成</button>
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

  function copy() {
    void navigator.clipboard?.writeText(view.answer).then(() => {
      setCopied(true)
      if (revertRef.current) clearTimeout(revertRef.current)
      revertRef.current = setTimeout(() => setCopied(false), 1200)
    })
  }

  return (
    <div className={styles.msg}>
      {(turn.stages.length > 0 || turn.phase === 'thinking' || turn.phase === 'streaming') && <ThinkingSteps turn={turn} />}
      {view.answer && (
        <div className={styles.body} data-streaming={turn.phase === 'streaming' ? '' : undefined}>
          {renderAnswer(view.answer, view.sources.length, n => onCite(n, view), view.sources)}
        </div>
      )}
      {turn.phase === 'stopped' && <span className={styles.stopped}>已停止</span>}
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
          {isLive && view.qaId && (
            <>
              <button type="button" className={`${styles.act} ${view.feedback === 'like' ? styles.on : ''}`} onClick={() => onFeedback('like')} aria-label="讚"><Icon name="thumbUp" size={15} /></button>
              <button type="button" className={`${styles.act} ${view.feedback === 'dislike' ? styles.on : ''}`} onClick={() => onFeedback('dislike')} aria-label="倒讚"><Icon name="thumbDown" size={15} /></button>
            </>
          )}
          <button type="button" className={styles.act} onClick={copy} aria-label="複製回答" title={copied ? '已複製' : '複製'}>
            <span className={styles.iconSwap} data-copied={copied ? '' : undefined}>
              <Icon name="copy" size={15} className={styles.iconCopy} />
              <Icon name="check" size={15} className={styles.iconCheck} />
            </span>
          </button>
          <button type="button" className={styles.act} onClick={onRegenerate} aria-label="重新生成" title="重新生成" disabled={disabled}>重新生成</button>
          {refCount > 0 && (
            <>
              <span className={styles.divider} />
              <button type="button" className={styles.srcBtn} onClick={() => onOpenSources(view)}>資料來源 {refCount}</button>
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
