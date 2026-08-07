import type { MouseEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { Icon } from '../../components/primitives/Icon'
import { Skeleton } from '../../components/primitives/Skeleton'
import type { CatalogSort, Market, RadarInstrumentItem } from '../../lib/radarSchemas'
import { CATALOG_WINDOW_DAYS } from './catalogState'
import { DirectionTag } from './DirectionTag'
import {
  BUCKET_DISPLAY, fmtDate, isReportStale, marketVar, RATING_BUCKET, RATING_DISPLAY,
  recentChange, STALE_REPORT_LABEL,
} from './radarFormat'
import { RatingDistBar } from './RatingDistBar'
import styles from './InstrumentTable.module.css'

/**
 * 欄位定義集中一處，`<colgroup>`、`<thead>` 與骨架列共用。
 *
 * `hint` 進欄名旁的 ⓘ：這幾欄的數字各自來自不同母體（涵蓋度算的是「提及此標的的全部
 * 研報」，分布算的是「已擷取到評等的券商」），不寫出來讀者只會把兩個數字當同一件事。
 *
 * `sortedBy` 讓表頭的 `aria-sort` 對得上目前的排序：排序控制在工具列的下拉，但
 * 「現在依哪一欄排」這件事只有表頭說得清楚，而讀屏使用者看不到那顆下拉的選中值。
 */
const COLUMNS: ReadonlyArray<{
  key: string
  label: string
  width: string
  align?: 'right'
  hint?: string
  sortedBy?: CatalogSort[]
}> = [
  { key: 'name', label: '標的', width: '16%', sortedBy: ['code'] },
  {
    key: 'rating',
    label: '共識評等',
    width: '11%',
    // 欄名刻意不是「最新評等」：這個值是各券商窗期內最新一筆評等的**中位立場**
    // （後端 `stance.rating` ＝ `median_rating`），不是最新一份研報寫的評等。
    hint: `各券商近 ${CATALOG_WINDOW_DAYS} 天內最新一筆評等的中位立場，不是最新一份研報的評等。`,
  },
  {
    key: 'change',
    label: '近期變化',
    width: '13%',
    hint: `近 ${CATALOG_WINDOW_DAYS} 天評等調升與調降的淨變化；評等沒有淨變動時，改看目標價的調整方向（只看方向，不列幅度）。`,
  },
  {
    key: 'dist',
    label: '評等分布',
    width: '19%',
    hint: `各券商近 ${CATALOG_WINDOW_DAYS} 天內最新一筆評等，歸入偏多（買進／加碼）、中立、偏空（減碼／賣出）三類。`,
  },
  {
    key: 'coverage',
    label: '資料涵蓋',
    // 15% 是量出來的：這格的內容（「11 家券商 · 177 份研報」）約 168px，1136px 容器下
    // 15% ＝ 170px 剛好單行；再窄就折成兩行（兩半各自 nowrap，只在中點斷）。
    width: '15%',
    hint: '提及此標的的券商與研報總數，含尚未擷取出訊號的研報——所以通常大於左邊已評等的家數。',
    sortedBy: ['reports', 'brokers'],
  },
  { key: 'latest', label: '最新研報', width: '12%', sortedBy: ['latest'] },
  { key: 'go', label: '查看', width: '14%', align: 'right' },
]

interface Props {
  items: RadarInstrumentItem[]
  sort: CatalogSort
  /** 詳情頁連結；`<Link>` 才讓中鍵／cmd+click／複製連結都拿得回來。 */
  hrefFor: (market: Market, code: string) => string
  /** 載入中：表頭留在 DOM 裡，只換 tbody（見下方說明）。 */
  loading?: boolean
  /** 三態內容（空／錯誤）。給定時取代 tbody 的資料列。 */
  fallback?: React.ReactNode
}

/**
 * 研究清單工作台的表格本體。
 *
 * **表頭在三態之間永遠不動**，只換 `<tbody>`：欄位在載入／空／有資料之間出現又消失
 * 會讓整頁高度連跳兩次，而那正是骨架要避免的事。
 */
export function InstrumentTable({ items, sort, hrefFor, loading, fallback }: Props) {
  return (
    <div className={styles.frame}>
      <table className={styles.table} aria-busy={loading || undefined}>
        <colgroup>
          {COLUMNS.map(c => <col key={c.key} style={{ width: c.width }} />)}
        </colgroup>
        <thead>
          <tr>
            {COLUMNS.map(c => (
              <th
                key={c.key}
                scope="col"
                className={c.align === 'right' ? styles.right : undefined}
                aria-sort={c.sortedBy?.includes(sort) ? 'descending' : undefined}
              >
                <span className={styles.thInner}>
                  {c.label}
                  {c.hint ? (
                    // title 給滑鼠，sr-only 給鍵盤與讀屏——只掛 title 的話，不用滑鼠的人
                    // 永遠讀不到這欄的口徑（同 ConsensusSnapshot 對「樣本不足」的處理）。
                    <span className={styles.hint} title={c.hint}>
                      <Icon name="info" size={13} />
                      <span className={styles.srOnly}>：{c.hint}</span>
                    </span>
                  ) : null}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {fallback ? (
            <tr>
              <td className={styles.fallback} colSpan={COLUMNS.length}>{fallback}</td>
            </tr>
          ) : loading ? (
            Array.from({ length: 8 }, (_, i) => (
              <tr key={i} className={styles.skelRow} data-testid="picker-skeleton">
                {COLUMNS.map(c => (
                  <td key={c.key}><Skeleton height={c.key === 'dist' ? 30 : 16} /></td>
                ))}
              </tr>
            ))
          ) : (
            items.map(item => (
              <InstrumentRow
                key={`${item.market}:${item.instrument_code}`}
                item={item}
                href={hrefFor(item.market, item.instrument_code)}
              />
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}

function InstrumentRow({ item, href }: { item: RadarInstrumentItem; href: string }) {
  const navigate = useNavigate()
  const stance = item.consensus?.stance
  const target = item.consensus?.target
  const change = recentChange(stance, target)
  const name = item.instrument_name || item.instrument_code
  const stale = isReportStale(item.latest_report_date)

  // 帶輔助鍵的點擊交給瀏覽器開新分頁；列上的連結自己會處理左鍵，所以已被處理過的
  // 事件（defaultPrevented）不再導覽一次。
  function openRow(e: MouseEvent) {
    if (e.defaultPrevented) return
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return
    navigate(href)
  }

  return (
    // 整列可點是滑鼠的便利，不是鍵盤的路徑：列刻意**不可聚焦**，每列只留「查看」一個
    // Tab 停點。列與連結各給一次 tabindex 會讓 52 檔變成 104 個停點，而兩者做的是同一件事。
    <tr className={styles.row} onClick={openRow}>
      {/* 內層再包一個 flex：`display: flex` 直接下在 <th>/<td> 上會讓它不再是 table-cell，
          `vertical-align: middle` 隨之失效，該格內容於是貼在列的頂端而其他格是置中的。 */}
      <th scope="row">
        <span className={styles.nameCell}>
          <span className={styles.name}>{name}</span>
          <span className={styles.code}>{item.instrument_code}</span>
          <span className={styles.badge} style={marketVar(item.market)}>
            {item.market_display || item.market}
          </span>
        </span>
      </th>

      <td>
        {stance ? (
          <span className={`${styles.rating} ${styles[RATING_BUCKET[stance.rating]]}`}>
            <span className={styles.srOnly}>中位立場：</span>
            {RATING_DISPLAY[stance.rating]}
            <span className={styles.bucket}>{BUCKET_DISPLAY[RATING_BUCKET[stance.rating]]}</span>
          </span>
        ) : (
          <span className={styles.muted}>—</span>
        )}
      </td>

      <td>
        {stance ? (
          <span className={styles.change} title={change.note}>
            <DirectionTag direction={change.direction} label={change.label} kind="rating" />
            {change.detail ? <span className={styles.changeN}>{change.detail}</span> : null}
          </span>
        ) : (
          <span className={styles.muted}>—</span>
        )}
      </td>

      <td className={styles.distCell}>
        {stance
          ? <RatingDistBar stance={stance} />
          : <span className={styles.muted}>資料擷取中</span>}
      </td>

      <td className={styles.coverage}>
        {/* 兩半各自 nowrap，中間的分隔點是唯一的斷點：不這樣切的話，折行會落在
            數字與量詞之間（實測折成「177 ／ 份研報」）。 */}
        <span className={styles.nowrap}>
          {item.broker_count} 家券商<span className={styles.sep} aria-hidden="true">·</span>
        </span>{' '}
        <span className={styles.nowrap}>{item.report_count} 份研報</span>
      </td>

      <td>
        <span className={styles.date}>
          {fmtDate(item.latest_report_date)}
          {stale ? <span className={styles.stale} title={STALE_REPORT_LABEL}>久未更新</span> : null}
        </span>
      </td>

      <td className={styles.right}>
        {/* 一家都沒給目標價時不承諾「各家目標價」——連結名稱是對目的地的承諾。 */}
        <Link
          className={styles.go}
          to={href}
          // 52 條逐字相同的連結名稱對讀屏等於 52 個無法分辨的連結。
          aria-label={`查看 ${name} ${item.instrument_code} 的${target ? '各家目標價' : '券商觀點'}`}
        >
          {target ? '查看各家目標價' : '查看券商觀點'}
          <Icon name="chevronDown" size={14} className={styles.goIcon} />
        </Link>
      </td>
    </tr>
  )
}
