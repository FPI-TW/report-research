import { useState } from 'react'
import { Callout } from '../../components/primitives/Callout'
import { Icon } from '../../components/primitives/Icon'
import { ThinkingSteps } from './ThinkingSteps'
import { renderAnswer } from '../../lib/askMarkdown'
import type { Turn } from '../../lib/askReducer'
import styles from './AssistantMessage.module.css'

interface Props {
  turn: Turn
  onCite: (n: number) => void
  onOpenSources: () => void
  onFeedback: (v: 'like' | 'dislike') => void
  onNoticeRetry: () => void
  onErrorRetry: () => void
}

export function AssistantMessage({ turn, onCite, onOpenSources, onFeedback, onNoticeRetry, onErrorRetry }: Props) {
  const [copied, setCopied] = useState(false)

  if (turn.phase === 'notice') {
    return <Callout variant="warning" action={{ label: '換個說法重新提問', onClick: onNoticeRetry }}>{turn.noticeText ?? '無法回答此問題'}</Callout>
  }
  if (turn.phase === 'error') {
    return <Callout variant="error" action={{ label: '重試', onClick: onErrorRetry }}>{turn.errorText ?? '查詢逾時或失敗'}</Callout>
  }

  const refCount = turn.sources.length + turn.extSources.length
  const showActions = turn.phase === 'done' && !turn.isOfftopic

  function copy() {
    void navigator.clipboard?.writeText(turn.answer).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200) })
  }

  return (
    <div className={styles.msg}>
      {(turn.stages.length > 0 || turn.phase === 'thinking' || turn.phase === 'streaming') && <ThinkingSteps turn={turn} />}
      {turn.answer && (
        <div className={styles.body} data-streaming={turn.phase === 'streaming' ? '' : undefined}>
          {renderAnswer(turn.answer, turn.sources.length, onCite)}
        </div>
      )}
      {showActions && (
        <div className={styles.actions}>
          {turn.qaId && (
            <>
              <button type="button" className={`${styles.act} ${turn.feedback === 'like' ? styles.on : ''}`} onClick={() => onFeedback('like')} aria-label="讚"><Icon name="thumbUp" size={15} /></button>
              <button type="button" className={`${styles.act} ${turn.feedback === 'dislike' ? styles.on : ''}`} onClick={() => onFeedback('dislike')} aria-label="倒讚"><Icon name="thumbDown" size={15} /></button>
            </>
          )}
          <button type="button" className={styles.act} onClick={copy} aria-label="複製回答" title={copied ? '已複製' : '複製'}><Icon name="copy" size={15} /></button>
          {refCount > 0 && (
            <>
              <span className={styles.divider} />
              <button type="button" className={styles.srcBtn} onClick={onOpenSources}>資料來源 {refCount}</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
