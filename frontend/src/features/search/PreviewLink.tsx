import type { CSSProperties, MouseEvent, ReactNode } from 'react'
import { Link } from 'react-router'
import type { ReportRow } from '../../lib/schemas'
import { reportHref } from '../report/readingFormat'

interface Props {
  row: ReportRow
  onPreview: (row: ReportRow) => void
  className?: string
  style?: CSSProperties
  'aria-label'?: string
  children: ReactNode
}

/**
 * 清單裡的一篇研報：點一下開預覽彈窗，但本體仍是連到閱讀頁的真連結——
 * cmd／ctrl＋點、中鍵、複製連結都照樣拿得到閱讀頁網址。
 */
export function PreviewLink({ row, onPreview, className, style, children, ...rest }: Props) {
  function onClick(e: MouseEvent<HTMLAnchorElement>) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    onPreview(row)
  }
  return (
    <Link
      to={reportHref(row.file_hash)}
      className={className}
      style={style}
      aria-haspopup="dialog"
      aria-label={rest['aria-label']}
      onClick={onClick}
    >
      {children}
    </Link>
  )
}
