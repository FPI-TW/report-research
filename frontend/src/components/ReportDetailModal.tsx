import { useQuery } from '@tanstack/react-query'
import { Modal } from './primitives/Modal'
import { getReportFull } from '../lib/searchApi'
import { displayTitle } from '../lib/displayTitle'
import styles from './ReportDetailModal.module.css'

interface Props {
  reportId: string | null
  fileName?: string
  onClose: () => void
}

function isPdfName(name: string): boolean {
  return name.toLowerCase().endsWith('.pdf')
}
function fileHref(id: string): string {
  return `/api/report/${encodeURIComponent(id)}/file`
}

export function ReportDetailModal({ reportId, fileName, onClose }: Props) {
  const open = reportId !== null
  const query = useQuery({
    queryKey: ['report-full', reportId],
    queryFn: () => getReportFull(reportId as string),
    enabled: open,
  })
  // 標題優先（fileName 是呼叫端在資料到齊前先給的暫時字樣）
  const title = displayTitle(query.data ?? { file_name: fileName })

  function body() {
    if (!open) return null
    if (query.isLoading) return <div className={styles.state}>載入中…</div>
    if (query.isError || !query.data) return <div className={styles.state}>報告載入失敗，請稍後再試。</div>
    const d = query.data
    const href = fileHref(d.report_id)
    if (d.has_file && isPdfName(d.file_name)) {
      return (
        <div className={styles.pdfWrap}>
          <div className={styles.actions}>
            <a className={styles.primary} href={href} target="_blank" rel="noopener noreferrer">在新分頁開啟</a>
            <a className={styles.ghost} href={href} download>下載原始檔</a>
            <span className={styles.hint}>Esc 關閉</span>
          </div>
          <iframe className={styles.frame} src={href} title={title} />
        </div>
      )
    }
    if (d.has_file) {
      const ext = d.file_name.split('.').pop()?.toUpperCase() ?? '檔案'
      return (
        <div className={styles.state}>
          <p>{ext} 文件 無法內嵌預覽，請下載查看。</p>
          <a className={styles.download} href={href} download>下載原始檔</a>
        </div>
      )
    }
    return <div className={styles.state}>找不到原始檔。</div>
  }

  return (
    <Modal open={open} onClose={onClose} title={title}>
      {body()}
    </Modal>
  )
}
