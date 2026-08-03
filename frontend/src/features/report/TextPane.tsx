import { Skeleton } from '../../components/primitives/Skeleton'
import type { ReadingText } from '../../lib/readingSchemas'
import styles from './TextPane.module.css'

interface Props {
  text: ReadingText | undefined
  isLoading: boolean
  isError: boolean
  /** 全文載入失敗時的重試（react-query refetch）。 */
  onRetry: () => void
  /** 有原始檔可下載。無檔時不得叫讀者去下載一個不存在的東西。 */
  hasFile: boolean
}

/**
 * 文字後備：內嵌不了原始檔時呈現正典文字。
 *
 * 這**不是**讀者可以切過來的檢視 —— 要不要渲染它由呼叫端（ReportPage 的 pdfViewable）
 * 依資料現實決定，PDF 研報永遠走不到這裡。所以本元件也不再提「切原文」：
 * 那個檢視不存在，講了就是死路。
 */
export function TextPane({ text, isLoading, isError, onRetry, hasFile }: Props) {
  // 只有真的失敗才說失敗：查詢剛啟用、尚未進 fetching 的那一拍 isLoading 仍為 false 而 data 未到，
  // 若把「沒資料」當錯誤會閃出假的錯誤態。故 !text 一律視為載入中。
  if (isError) {
    return (
      <div className={styles.stage}>
        <div className={styles.state} role="status">
          <p className={styles.stateText}>全文載入失敗。</p>
          <button type="button" className={styles.retry} onClick={onRetry}>重試</button>
        </div>
      </div>
    )
  }

  if (isLoading || !text) {
    return (
      <div className={styles.stage}>
        <article className={styles.reader} aria-busy="true" data-testid="text-skeleton">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} width={i % 3 === 2 ? '68%' : '100%'} height={14} radius={4} style={{ marginBottom: 12 }} />
          ))}
        </article>
      </div>
    )
  }

  return (
    <div className={styles.stage}>
      <article className={styles.reader}>
        <div className={styles.note}>
          此為原始檔的文字抽取結果，圖表與表格排版不會保留{hasFile ? ' — 需要原始版面請由頁首下載原始檔' : ''}。
        </div>
        <div className={styles.body}>{text.text}</div>
        {text.truncated && (
          <p className={styles.truncated}>
            全文過長，此處僅顯示前段{hasFile ? ' — 需要完整內容請由頁首下載原始檔' : ''}。
          </p>
        )}
      </article>
    </div>
  )
}
