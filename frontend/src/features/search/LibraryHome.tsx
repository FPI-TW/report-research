import type { CSSProperties } from 'react'
import type { ReportRow } from '../../lib/schemas'
import { BriefTile } from './BriefTile'
import { HeadlineTile } from './HeadlineTile'
import { HomeSkeleton } from './LibrarySkeleton'
import { LatestTile } from './LatestTile'
import { RatingChangesTile } from './RatingChangesTile'
import { useLibraryHome } from './useLibraryHome'
import styles from './LibraryHome.module.css'

/** 「更多最新入庫」的列數（頭條之後的 5 篇）。 */
const LATEST_ROWS = 5

interface Props {
  rows: ReportRow[]
  latestId: string | null
  totalReports: number
  onPreview: (row: ReportRow) => void
  onSeeAll: () => void
}

const stagger = (i: number) => ({ '--i': i }) as CSSProperties

/**
 * 研報庫首頁 bento（6 欄、gap 14）：頭條大卡（跨 3 欄 2 列）、最新評等變動、今日簡報、
 * 更多最新入庫。附屬資料都到齊（或確定沒有）才一起進場，期間顯示同版面的骨架。
 */
export function LibraryHome({ rows, latestId, totalReports, onPreview, onSeeAll }: Props) {
  const [head, ...rest] = rows
  const { ready, doc, consensus, changes, brief } = useLibraryHome(head)
  if (!head || !ready) return <HomeSkeleton />

  const latest = rest.slice(0, LATEST_ROWS)
  return (
    <div className={styles.bento}>
      <HeadlineTile
        className={`${styles.cell} ${styles.headline}`}
        style={stagger(0)}
        row={head}
        isLatest={head.report_id === latestId}
        doc={doc.data}
        consensus={consensus.data}
      />
      <RatingChangesTile
        className={`${styles.cell} ${styles.changes}`}
        style={stagger(1)}
        items={changes.data?.items}
        failed={changes.isError}
        onRetry={() => { void changes.refetch() }}
      />
      <BriefTile
        className={`${styles.cell} ${styles.brief}`}
        style={stagger(2)}
        envelope={brief.data}
        failed={brief.isError}
        onRetry={() => { void brief.refetch() }}
      />
      {latest.length > 0 && (
        <LatestTile
          className={`${styles.cell} ${styles.latest}`}
          style={stagger(3)}
          rows={latest}
          totalReports={totalReports}
          onPreview={onPreview}
          onSeeAll={onSeeAll}
        />
      )}
    </div>
  )
}
