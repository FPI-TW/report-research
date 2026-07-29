import { useReducedMotion } from 'motion/react'
import type { CSSProperties } from 'react'
import { displayTitle } from '../../lib/displayTitle'
import { marketColor, marketLabel } from '../../lib/meta'
import { springHover } from '../../lib/motionTokens'
import type { ReportRow } from '../../lib/schemas'
import { MotionLink } from '../../components/primitives/MotionLink'
import { Pressable } from '../../components/primitives/Pressable'
import { FeatureTile } from './FeatureTile'
import { Spectrum, type SpectrumSlice } from './Spectrum'
import { reportHref } from '../report/readingFormat'
import styles from './BentoWall.module.css'

/** 圖例只列前 5 大市場，其餘留給色譜本身表達——避免圖例喧賓奪主。 */
const LEGEND_MAX = 5
/** 清單磚顯示的則數（頭條 1 + 清單 5 = 最新 6 篇）。 */
const LIST_ROWS = 5

interface Props {
  rows: ReportRow[]
  latestId: string | null
  totalReports: number
  composition: SpectrumSlice[]
  monthLabel: string
  onSeeAll: () => void
}

function monthOf(date: string | null): string {
  if (!date) return ''
  const [y, m] = date.slice(0, 10).split('-')
  return y && m ? `${y} 年 ${Number(m)} 月` : ''
}

export { monthOf }

/**
 * 瀏覽態的 Bento 簡報牆：頭條大磚（2×2）＋數據磚＋語料庫組成磚＋更多清單磚。
 * 首頁＝每日簡報面，不是行銷招牌——品牌已在左欄 SideRail，此處不重複。
 */
export function BentoWall({
  rows, latestId, totalReports, composition, monthLabel, onSeeAll,
}: Props) {
  const reduced = useReducedMotion()
  const [head, ...rest] = rows
  const listRows = rest.slice(0, LIST_ROWS)
  const legend = composition.slice(0, LEGEND_MAX)
  const compTotal = composition.reduce((n, s) => n + s.count, 0)

  return (
    <div className={styles.grid}>
      {head && (
        <FeatureTile
          className={styles.feature}
          row={head}
          mode="browse"
          isLatest={head.report_id === latestId}
        />
      )}

      <div className={`${styles.tile} ${styles.stat}`}>
        <span className={styles.num}>{totalReports.toLocaleString()}</span>
        <span className={styles.lbl}>篇研報 · DOCS</span>
      </div>

      <div className={`${styles.tile} ${styles.stat}`}>
        <span className={`${styles.num} ${styles.gold}`}>{composition.length}</span>
        <span className={styles.lbl}>涵蓋市場 · MARKETS</span>
      </div>

      <div className={`${styles.tile} ${styles.comp}`}>
        <span className={styles.h}>語料庫組成</span>
        <Spectrum slices={composition} label="語料庫市場組成" className={styles.spectrum} />
        <div className={styles.lgs}>
          {legend.map(s => (
            <span key={s.market} style={{ '--c': marketColor(s.market) } as CSSProperties}>
              <i />
              <span className={styles.mk}>{marketLabel(s.market)}</span>{' '}
              {compTotal ? Math.round((s.count / compTotal) * 100) : 0}%
            </span>
          ))}
        </div>
      </div>

      {listRows.length > 0 && (
        <div className={`${styles.tile} ${styles.list}`}>
          <div className={styles.lh}>
            <span className={styles.t}>更多最新入庫</span>
            {monthLabel && <span className={styles.m}>{monthLabel}</span>}
            <Pressable as="span" className={styles.seeAll} onClick={onSeeAll}>
              查看全部 {totalReports.toLocaleString()} 篇 ›
            </Pressable>
          </div>
          {listRows.map(r => {
            const date = (r.report_date ?? '').slice(0, 10)
            return (
              // 真連結：cmd+click／中鍵／複製連結（瀏覽態無命中，故不帶 chunk）
              <MotionLink
                key={r.report_id}
                className={styles.lrow}
                to={reportHref(r.file_hash)}
                style={{ '--c': marketColor(r.market ?? '') } as CSSProperties}
                whileHover={reduced ? undefined : { x: 2, transition: springHover }}
                aria-label={displayTitle(r)}
              >
                <span className={styles.code}>{marketLabel(r.market ?? '')}</span>
                <span className={styles.ltitleWrap}>
                  <span className={styles.ltitle}>{displayTitle(r)}</span>
                  {r.source && <span className={styles.lsrc}>{r.source}</span>}
                </span>
                <span className={styles.ldate}>{date.slice(5)}</span>
              </MotionLink>
            )
          })}
        </div>
      )}
    </div>
  )
}
