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

  return (
    <div className={styles.stage}>
      <iframe className={styles.frame} src={href} title={doc.file_name} />
    </div>
  )
}
