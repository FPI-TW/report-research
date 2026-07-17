import { motion, useReducedMotion, type Variants } from 'motion/react'
import { useMemo } from 'react'
import { marketLabel, marketTint, instrumentLabel } from '../../lib/meta'
import { highlight } from '../../lib/highlight'
import { revealTransition, revealVariantsFor, springHover, TF_DUR, TF_EASE_OUT, tfInstant } from '../../lib/motionTokens'
import type { ReportRow } from '../../lib/schemas'
import type { SearchMode } from '../../lib/searchFilters'
import styles from './ResultCard.module.css'

interface Props {
  row: ReportRow
  mode: SearchMode
  isLatest: boolean
  terms: string[]
  onOpen: (id: string, fileName: string) => void
  /** 清單中的序號，用於封頂 stagger 進場；預設 0。 */
  index?: number
}

function truncate(s: string | null, n: number): string {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '…' : s
}

/** 「查看全文」淡入：靠 Motion 把父層 whileHover/whileFocus 的 variant 標籤傳播下來，
 *  毋須把 hover/focus 狀態拉到 JS。標籤須與父層 variants 同名。 */
const ctaVariants: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 0 },
  hover: { opacity: 1, transition: { duration: TF_DUR.d1, ease: TF_EASE_OUT } },
}

/** 高密度列表列：市場｜標題＋標的＋命中片段｜相關度＋來源日期（等高欄位、可掃讀）。 */
export function ResultCard({ row, mode, isLatest, terms, onOpen, index = 0 }: Props) {
  const reduced = useReducedMotion()
  const targets = [...(row.stock_targets ?? []), ...(row.futures_targets ?? [])]
  const pills = [...(row.instrument_types ?? []).map(instrumentLabel), ...targets]
  const date = (row.report_date ?? '').slice(0, 10)
  const open = () => onOpen(row.report_id, row.file_name)
  const pct = mode === 'search'
    ? Math.max(4, Math.min(100, Math.round((row.best_score ?? 0) * 100)))
    : 0
  const snippet = row.passages?.[0]?.content ?? ''

  // hover 抬升與 CTA 淡入共用 'hover' 標籤，父層才能把狀態傳播給子元素。
  // 抬升的 transition 必須寫在 variant 內，否則會吃到帶 stagger 延遲的進場 transition。
  const cardVariants = useMemo<Variants>(() => ({
    ...revealVariantsFor('up'),
    hover: { y: reduced ? 0 : -6, transition: reduced ? tfInstant : springHover },
  }), [reduced])

  return (
    <motion.article
      className={styles.card}
      variants={cardVariants}
      initial="hidden"
      animate="visible"
      transition={revealTransition(reduced, index)}
      whileHover="hover"
      // 原 CSS 為 .card:hover .cta, .card:focus-visible .cta——鍵盤焦點也要看得到提示
      whileFocus="hover"
      role="button"
      tabIndex={0}
      aria-label={row.file_name}
      onClick={open}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open() } }}
    >
      <div className={styles.chipCol}>
        <span className={styles.badge} style={marketTint(row.market ?? '')}>
          <span className={styles.badgeDot} style={{ background: 'currentColor' }} aria-hidden="true" />
          {marketLabel(row.market ?? '')}
        </span>
      </div>

      <div className={styles.main}>
        <div className={styles.titleRow}>
          <span className={styles.title}>{row.file_name}</span>
          {isLatest && <span className={styles.latest}>最新</span>}
        </div>
        {pills.length > 0 && <div className={styles.tags}>{pills.join(' · ')}</div>}
        {mode === 'search' ? (
          snippet && (
            <div className={styles.snippet}>{highlight(snippet.slice(0, 160), terms)}{snippet.length > 160 ? '…' : ''}</div>
          )
        ) : (
          row.summary && <div className={styles.summary}>{truncate(row.summary, 96)}</div>
        )}
      </div>

      <div className={styles.right}>
        {mode === 'search' && (
          <span className={styles.scoreRow}>
            <span className={styles.scoreTrack}>
              <motion.span
                className={styles.scoreFill}
                initial={{ width: 0 }}
                animate={{ width: `${pct}%` }}
                transition={reduced ? tfInstant : { duration: TF_DUR.d4, ease: TF_EASE_OUT, delay: Math.min(index, 8) * 0.04 }}
              />
            </span>
            <span className={styles.scoreNum}>{pct}%</span>
          </span>
        )}
        <span className={styles.meta}>{[row.source, date].filter(Boolean).join(' · ')}</span>
        <motion.span className={styles.cta} variants={ctaVariants}>查看全文 ›</motion.span>
      </div>
    </motion.article>
  )
}
