import { reportFileHref } from '../../lib/readingApi'
import type { ReadingDoc } from '../../lib/readingSchemas'
import styles from './PdfPane.module.css'

interface Props {
  doc: ReadingDoc
}

/** 原文檢視：PDF 內嵌；非 PDF／無檔時給可下載的替代說明（不是錯誤）。 */
export function PdfPane({ doc }: Props) {
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

  // PDF：桌面瀏覽器多能內嵌，但 iOS/多數 Android 不會在 <iframe> 內渲染 PDF（只留空白），
  // 且「無法內嵌」不觸發 onError → 前端無從偵測。故一律附一條可「開新分頁／下載」的逃生口，
  // 空白框才不至於是死路；桌面上它同時是個順手的捷徑。放在框上方，手機讀者第一眼就看得到。
  return (
    <div className={styles.stage}>
      <div className={styles.pdfWrap}>
        <div className={styles.pdfBar}>
          <span className={styles.pdfHint}>若下方無法顯示 PDF</span>
          <a className={styles.pdfLink} href={href} target="_blank" rel="noopener noreferrer">在新分頁開啟</a>
          <span className={styles.pdfDot} aria-hidden="true">·</span>
          <a className={styles.pdfLink} href={href} download>下載</a>
        </div>
        <iframe className={styles.frame} src={href} title={doc.file_name} />
      </div>
    </div>
  )
}
