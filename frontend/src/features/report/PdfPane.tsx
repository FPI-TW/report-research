import { Suspense, lazy } from 'react'
import { displayTitle } from '../../lib/displayTitle'
import { reportFileHref } from '../../lib/readingApi'
import type { ReadingDoc } from '../../lib/readingSchemas'
import type { JumpRequest, JumpResult } from './pdf/PdfViewer'
import { ViewerBoundary } from './pdf/ViewerBoundary'
import styles from './PdfPane.module.css'

// 懶載入：PDFium 的 WASM 與外掛加起來體積可觀，不該讓問答與檢索頁一起背。
const PdfViewer = lazy(() => import('./pdf/PdfViewer'))

interface Props {
  doc: ReadingDoc
  /** 左欄摘錄的跳轉請求，透傳給自訂檢視器 */
  jump?: JumpRequest | null
  onJumpResult?: (r: JumpResult) => void
  /**
   * 引擎降級成瀏覽器內建檢視時通知呼叫端。
   * 內建檢視**沒有任何搜尋能力**，所以摘錄必須整批退回非互動 ——
   * 少了這條通道，降級之後每一次點擊都會變成「找不到」，看起來像資料壞了。
   */
  onDegraded?: () => void
}

/**
 * 瀏覽器內建檢視（降級路徑）。
 *
 * iOS/多數 Android 不會在 <iframe> 內渲染 PDF（只留空白），且「無法內嵌」不觸發
 * onError → 前端無從偵測。故一律附一條可「開新分頁／下載」的逃生口，空白框才不至於
 * 是死路；桌面上它同時是個順手的捷徑。放在框上方，手機讀者第一眼就看得到。
 */
function BuiltInViewer({ href, title }: { href: string; title: string }) {
  return (
    <div className={styles.pdfWrap}>
      <div className={styles.pdfBar}>
        <span className={styles.pdfHint}>若下方無法顯示 PDF</span>
        <a className={styles.pdfLink} href={href} target="_blank" rel="noopener noreferrer">在新分頁開啟</a>
        <span className={styles.pdfDot} aria-hidden="true">·</span>
        <a className={styles.pdfLink} href={href} download>下載</a>
      </div>
      <iframe className={styles.frame} src={href} title={title} />
    </div>
  )
}

/**
 * 文件區主路徑：自訂檢視器；非 PDF／無檔時給可下載的替代說明（不是錯誤）。
 *
 * 那兩個分支**不是死碼**：ReportPage 只有在「內嵌不了且有全文」時才改掛 TextPane，
 * 內嵌不了又沒有全文的研報仍然送到這裡，靠它們給出終態。改 ReportPage 的分派條件時
 * 要記得這件事，否則那一格會退化成永遠的載入骨架。
 */
export function PdfPane({ doc, jump = null, onJumpResult, onDegraded }: Props) {
  if (!doc.has_file) {
    return (
      <div className={styles.stage}>
        <div className={styles.state} role="status">
          <p className={styles.stateText}>找不到原始檔。</p>
        </div>
      </div>
    )
  }

  const href = reportFileHref(doc.report_id)
  const title = displayTitle(doc)

  if (!doc.is_pdf) {
    const ext = doc.file_name.split('.').pop()?.toUpperCase() ?? '檔案'
    return (
      <div className={styles.stage}>
        <div className={styles.state} role="status">
          <p className={styles.stateText}>{ext} 文件無法內嵌預覽，請下載查看。</p>
          <a className={styles.download} href={href} download>下載原始檔</a>
        </div>
      </div>
    )
  }

  // 降級決策集中在這裡：引擎載入失敗（邊界）與程式碼分割失敗（Suspense 之外的
  // 載入錯誤同樣會被邊界接住）都退回同一個內建檢視，不會出現兩套降級規則。
  return (
    <div className={`${styles.stage} ${styles.stageViewer}`}>
      {/* key 綁 href：邊界只有 failed 一個狀態，重建不會丟東西，但**不綁的話**在同一個
          ReportPage 實例內換研報時 failed 會沾黏，讓下一篇明明能開也直接落到內建檢視。
          **不要**給 PdfViewer 加 key —— 那會讓同篇的任何重渲染都重載一次引擎。 */}
      <ViewerBoundary
        key={href}
        fallback={<BuiltInViewer href={href} title={title} />}
        onError={onDegraded}
      >
        <Suspense fallback={<div className={styles.booting} role="status">正在啟動 PDF 引擎…</div>}>
          <PdfViewer url={href} title={title} jump={jump} onJumpResult={onJumpResult} />
        </Suspense>
      </ViewerBoundary>
    </div>
  )
}
