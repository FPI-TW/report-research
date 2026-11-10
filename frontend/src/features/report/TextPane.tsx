import { AlertCircle } from 'lucide-react'
import type { ReadingText } from '../../lib/readingSchemas'
import { StateBox } from './ReportStates'
import stateStyles from './ReportStates.module.css'
import styles from './TextPane.module.css'
import { t } from '@/i18n'

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
        <StateBox
          role="status"
          onCanvas
          icon={<AlertCircle size={18} strokeWidth={1.8} />}
          title={t('report.textPane.couldnTLoadTheFull')}
          desc={t('report.textPane.theConnectionTimedOutTry')}
        >
          <button type="button" className={stateStyles.btn} onClick={onRetry}>{t('report.textPane.retry')}</button>
        </StateBox>
      </div>
    )
  }

  if (isLoading || !text) {
    return (
      <div className={styles.stage}>
        <article className={styles.reader} aria-busy="true" data-testid="text-skeleton">
          {Array.from({ length: 8 }, (_, i) => (
            <span key={i} className={styles.sk} style={{ width: i % 3 === 2 ? '68%' : '100%' }} />
          ))}
        </article>
      </div>
    )
  }

  return (
    <div className={styles.stage}>
      <article className={styles.reader}>
        <div className={styles.note}>
          {t('report.textPane.thisIsTheTextExtracted')}{hasFile ? t('report.textPane.downloadTheOriginalForThe') : ''}{t('report.textPane.text')}
        </div>
        <div className={styles.body}>{text.text}</div>
        {text.truncated && (
          <p className={styles.truncated}>
            {t('report.textPane.theFullTextIsToo')}{hasFile ? t('report.textPane.downloadTheOriginalForThe2') : ''}{t('report.textPane.text')}
          </p>
        )}
      </article>
    </div>
  )
}
