import type { CSSProperties, MouseEvent, ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { marketLabel } from '../../lib/meta'
import type { CatalogSort, Market, RadarInstrumentItem } from '../../lib/radarSchemas'
import { fmtShortDate, RATING_DISPLAY } from './radarFormat'
import { changeParts } from './catalogState'
import { ratingParts, ratingPartsText } from './consensusData'
import { RatingDistBar } from './RatingDistBar'
import shared from './shared.module.css'
import styles from './InstrumentTable.module.css'

/**
 * 欄位定義：表頭與骨架共用。`sortedBy` 讓表頭的 `aria-sort` 對得上工具列的排序下拉——
 * 「現在依哪一欄排」只有表頭說得清楚，而讀屏使用者看不到下拉的選中值。
 */
const COLUMNS: ReadonlyArray<{
  key: string
  label: string
  align?: 'right'
  sortedBy?: CatalogSort[]
}> = [
  { key: 'name', label: '標的', sortedBy: ['code'] },
  { key: 'rating', label: '共識評等' },
  { key: 'dist', label: '評等分布' },
  { key: 'change', label: '近期變化' },
  { key: 'coverage', label: '資料涵蓋', sortedBy: ['reports', 'brokers'] },
  { key: 'latest', label: '最新研報', align: 'right', sortedBy: ['latest'] },
]

const SKELETON_ROWS = 5

interface Props {
  items: RadarInstrumentItem[]
  sort: CatalogSort
  /** 詳情頁連結；用 `<Link>` 才讓中鍵、cmd+click、複製連結都拿得到。 */
  hrefFor: (market: Market, code: string) => string
  loading?: boolean
  /** 錯誤或空白狀態。給定時取代資料列，表頭留著。 */
  fallback?: ReactNode
}

/**
 * 券商共識清單（交接包「券商共識」清單）。
 *
 * 外觀是一張卡片裡的格線列，語意是 ARIA 表格：每列的標的名稱是連結（唯一的 Tab 停點），
 * 整列可點只是滑鼠的便利。表頭在載入、空白、錯誤之間都留著，只換下面的內容。
 */
export function InstrumentTable({ items, sort, hrefFor, loading, fallback }: Props) {
  return (
    <div className={`${shared.tile} ${styles.frame}`} role="table" aria-label="券商共識標的" aria-busy={loading || undefined}>
      <div role="rowgroup">
        <div role="row" className={`${styles.grid} ${styles.head}`}>
          {COLUMNS.map(c => (
            <span
              key={c.key}
              role="columnheader"
              className={c.align === 'right' ? styles.right : undefined}
              aria-sort={c.sortedBy?.includes(sort) ? (sort === 'code' ? 'ascending' : 'descending') : undefined}
            >
              {c.label}
            </span>
          ))}
        </div>
      </div>
      <div role="rowgroup">
        {fallback ? (
          <div role="row">
            <div role="cell" aria-colspan={COLUMNS.length}>{fallback}</div>
          </div>
        ) : loading ? (
          Array.from({ length: SKELETON_ROWS }, (_, i) => <SkeletonRow key={i} />)
        ) : (
          items.map((item, i) => (
            <InstrumentRow
              key={`${item.market}:${item.instrument_code}`}
              item={item}
              index={i}
              href={hrefFor(item.market, item.instrument_code)}
            />
          ))
        )}
      </div>
    </div>
  )
}

function SkeletonRow() {
  return (
    <div role="row" className={`${styles.grid} ${styles.row} ${styles.skelRow}`} data-testid="picker-skeleton">
      <span role="cell" className={styles.skelName}>
        <span className={shared.skel} style={{ width: '60%', height: 14 }} />
        <span className={shared.skel} style={{ width: '36%', height: 10 }} />
      </span>
      <span role="cell"><span className={shared.skel} style={{ width: 44, height: 22 }} /></span>
      <span role="cell"><span className={shared.skel} style={{ width: '100%', height: 6 }} /></span>
      <span role="cell"><span className={shared.skel} style={{ width: 56, height: 12 }} /></span>
      <span role="cell"><span className={shared.skel} style={{ width: '80%', height: 12 }} /></span>
      <span role="cell" className={styles.right}>
        <span className={shared.skel} style={{ width: 40, height: 12, marginLeft: 'auto' }} />
      </span>
    </div>
  )
}

function InstrumentRow({ item, index, href }: { item: RadarInstrumentItem; index: number; href: string }) {
  const navigate = useNavigate()
  const stance = item.consensus?.stance
  const name = item.instrument_name || item.instrument_code
  const parts = stance ? ratingParts(stance.distribution) : []
  const changes = changeParts(item)

  // 帶輔助鍵的點擊交給瀏覽器（開新分頁）；點在連結本身時連結已處理過，不再導覽一次。
  function openRow(e: MouseEvent) {
    if (e.defaultPrevented) return
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return
    navigate(href)
  }

  return (
    <div
      role="row"
      className={`${styles.grid} ${styles.row}`}
      style={{ '--i': Math.min(index, 6) } as CSSProperties}
      onClick={openRow}
    >
      <span role="rowheader" className={styles.nameCell}>
        <Link className={styles.name} to={href}>{name}</Link>
        <span className={styles.sub}>
          <span className={shared.code}>{item.instrument_code}</span>
          <span className={styles.mkt} style={{ color: `var(--mkt-${item.market}, var(--mkt-fallback))` }}>
            {item.market_display || marketLabel(item.market)}
          </span>
        </span>
      </span>

      <span role="cell">
        {stance ? (
          <span className={`${shared.badge} ${shared[stance.rating]}`}>{RATING_DISPLAY[stance.rating]}</span>
        ) : (
          <span className={styles.muted}>—</span>
        )}
      </span>

      <span role="cell" className={styles.dist}>
        {parts.length ? (
          <>
            <RatingDistBar parts={parts} height={6} />
            <span className={`${styles.distText} ${shared.num}`}>{ratingPartsText(parts)}</span>
          </>
        ) : (
          <span className={styles.muted}>{stance ? '尚無評等' : '資料擷取中'}</span>
        )}
      </span>

      <span role="cell" className={styles.change}>
        {changes.length
          ? changes.map(c => (
            <span key={c.text} className={`${shared[c.tone]} ${styles.changePart} ${shared.num}`}>{c.text}</span>
          ))
          : <span className={styles.muted}>{stance ? '無變動' : '—'}</span>}
      </span>

      <span role="cell" className={styles.coverage}>
        <span className={shared.num}>{item.broker_count}</span> 家券商
        <span className={styles.reports}><span className={shared.num}>{item.report_count}</span> 篇</span>
      </span>

      <span role="cell" className={`${styles.date} ${styles.right} ${shared.num}`}>
        {fmtShortDate(item.latest_report_date)}
      </span>
    </div>
  )
}
