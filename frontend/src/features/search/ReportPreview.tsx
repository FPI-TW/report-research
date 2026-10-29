import { useState } from 'react'
import { Link } from 'react-router'
import { X } from 'lucide-react'
import { Modal } from '../../components/primitives/Modal'
import { displayTitle } from '../../lib/displayTitle'
import { marketColor, marketLabel } from '../../lib/meta'
import type { ReportRow } from '../../lib/schemas'
import { reportHref } from '../report/readingFormat'
import { askHref, fullDate } from './libraryFormat'
import atoms from './atoms.module.css'
import styles from './ReportPreview.module.css'

interface Props {
  row: ReportRow | null
  onClose: () => void
}

/**
 * 研報預覽彈窗（580px）：市場、券商、日期、標題、標的、摘要，「開啟閱讀頁」「就這篇提問」。
 * 關閉鈕排在最前面，Modal 的焦點陷阱開啟時就落在它上面（交接包「焦點管理」）。
 */
export function ReportPreview({ row, onClose }: Props) {
  // 關閉時 row 先變 null、面板還在播離場：留著最後一篇，離場期間內容才不會先被清空
  const [shown, setShown] = useState(row)
  if (row && row !== shown) setShown(row)
  return (
    <Modal open={row !== null} onClose={onClose} bare ariaLabel={shown ? displayTitle(shown) : '研報預覽'} className={styles.panel}>
      {shown && <PreviewBody row={shown} onClose={onClose} />}
    </Modal>
  )
}

function PreviewBody({ row, onClose }: { row: ReportRow; onClose: () => void }) {
  const market = row.market ?? ''
  const title = displayTitle(row)
  const targets = [...(row.stock_targets ?? []), ...(row.futures_targets ?? [])]
  return (
    <div className={styles.body}>
      <div className={styles.meta}>
        <span className={styles.market} style={{ color: marketColor(market) }}>{marketLabel(market)}</span>
        {row.source && <span className={styles.broker}>{row.source}</span>}
        {row.report_date && <span className={`${atoms.num} ${styles.date}`}>{fullDate(row.report_date)}</span>}
        <button type="button" className={styles.close} aria-label="關閉" onClick={onClose}>
          <X size={15} strokeWidth={1.8} aria-hidden />
        </button>
      </div>
      <h2 className={styles.title}>{title}</h2>
      {targets.length > 0 && (
        <div className={styles.targets}>
          {targets.map(t => <span key={t} className={atoms.codeChip}>{t}</span>)}
        </div>
      )}
      {row.summary && <p className={styles.summary}>{row.summary}</p>}
      <div className={styles.actions}>
        <Link className={atoms.btn} to={reportHref(row.file_hash)}>開啟閱讀頁</Link>
        <Link className={`${atoms.btn} ${atoms.btnPrimary}`} to={askHref(title)}>就這篇提問</Link>
      </div>
    </div>
  )
}
