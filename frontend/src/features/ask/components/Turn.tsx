import { useState } from 'react'
import type { TurnState } from '../lib/conversation'
import { renderMarkdown } from '../lib/markdown'
import { ProcessSteps } from './ProcessSteps'
import { sendFeedback } from '../api'
import { mColor, mLabel, fmtDate } from '../../search/components/meta'
import styles from './AskPage.module.css'

interface TurnProps {
  turn: TurnState
  onCite: (reportId: string) => void
  onFeedback?: (qaId: string, value: 'like' | 'dislike') => void
}

function getDomain(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
}

export function Turn({ turn, onCite, onFeedback }: TurnProps) {
  const [fb, setFb] = useState<'like' | 'dislike' | null>(turn.feedback)

  const onCiteN = (n: number) => {
    const s = turn.sources.find((x) => x.n === n)
    if (s) onCite(s.report_id)
  }

  const feedback = (v: 'like' | 'dislike') => {
    if (!turn.qaId) return
    setFb(v)
    if (onFeedback) onFeedback(turn.qaId, v)
    else void sendFeedback(turn.qaId, v)
  }

  const copy = () => {
    if (navigator.clipboard && window.isSecureContext) {
      void navigator.clipboard.writeText(turn.answer)
    } else {
      const ta = document.createElement('textarea')
      ta.value = turn.answer
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.focus()
      ta.select()
      try {
        document.execCommand('copy')
      } catch {
        /* ignore */
      }
      document.body.removeChild(ta)
    }
  }

  const isNotice = turn.phase === 'notice'
  const isError = turn.phase === 'error'
  const isStreaming = turn.phase === 'streaming'
  const isDone = turn.phase === 'done'

  return (
    <div className={styles.turn}>
      {/* 問題泡泡 */}
      <div className={styles.msgUser} data-testid="ask-q">
        {turn.q}
      </div>

      {/* 處理步驟面板 */}
      <ProcessSteps turn={turn} />

      {/* 主要內容：離題卡 / 錯誤 / 答案 */}
      {isNotice ? (
        <div className={styles.notice} data-testid="ask-notice">
          <div className={styles.noticeTitle}>無法回答此問題</div>
          <div>{turn.notice}</div>
        </div>
      ) : isError ? (
        <div className={styles.error} data-testid="ask-error">
          {turn.errorMsg}
        </div>
      ) : (
        <div className={styles.msgBot} data-testid="ask-answer" aria-live="polite">
          {renderMarkdown(turn.answer, turn.sources.length, onCiteN)}
          {isStreaming && <span className={styles.caret} />}
        </div>
      )}

      {/* 來源清單（notice 時不顯示） */}
      {turn.sources.length > 0 && !isNotice && (
        <div className={styles.sources} data-testid="ask-sources">
          {turn.sources.map((s) => (
            <button
              key={s.n}
              type="button"
              className={styles.src}
              data-testid="ask-src"
              onClick={() => onCite(s.report_id)}
            >
              <span className={styles.srcN}>{s.n}</span>
              {s.market && (
                <span className={styles.badge} style={{ background: mColor(s.market) }}>
                  {mLabel(s.market)}
                </span>
              )}
              <span className={styles.srcName}>{s.file_name}</span>
              {s.report_date && <span className={styles.srcDate}>{fmtDate(s.report_date)}</span>}
              {s.is_latest && <span className={styles.srcLatest}>最新</span>}
            </button>
          ))}
        </div>
      )}

      {/* 外部來源（notice 時不顯示） */}
      {turn.extSources.length > 0 && !isNotice && (
        <div className={styles.ext} data-testid="ask-ext">
          {turn.extSources.map((s, i) => (
            <a
              key={i}
              className={styles.extLink}
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              <span className={styles.extBadge}>網路</span>
              <span>{s.title || s.url}</span>
              <span className={styles.extDomain}>{getDomain(s.url)}</span>
            </a>
          ))}
        </div>
      )}

      {/* 動作列：done 且非 notice 時顯示 */}
      {isDone && !isNotice && (
        <div className={styles.actions}>
          <button
            type="button"
            aria-label="讚"
            className={fb === 'like' ? styles.on : undefined}
            disabled={!turn.qaId}
            onClick={() => feedback('like')}
          >
            讚
          </button>
          <button
            type="button"
            aria-label="倒讚"
            className={fb === 'dislike' ? styles.on : undefined}
            disabled={!turn.qaId}
            onClick={() => feedback('dislike')}
          >
            倒讚
          </button>
          <button type="button" aria-label="複製回答" onClick={copy}>
            複製
          </button>
        </div>
      )}
    </div>
  )
}
