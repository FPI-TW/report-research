import type { CSSProperties } from 'react'
import { Link } from 'react-router'
import { ArrowRight, ChevronRight } from 'lucide-react'
import type { RatingChange } from '../../lib/librarySchemas'
import { instrumentHref } from '../radar/catalogState'
import { RATING_LABEL, shortDate } from './libraryFormat'
import atoms from './atoms.module.css'
import styles from './RatingChangesTile.module.css'

interface Props {
  items?: RatingChange[]
  failed: boolean
  onRetry: () => void
  className?: string
  style?: CSSProperties
}

function ChangeRow({ c }: { c: RatingChange }) {
  const name = c.instrument_name || c.instrument_code
  const up = c.direction === 'up'
  const broker = c.broker_display || c.broker
  return (
    <Link
      className={styles.row}
      to={instrumentHref(new URLSearchParams(), c.market, c.instrument_code)}
      aria-label={`${name} ${c.instrument_code}，${broker}，${RATING_LABEL[c.rating_from]}${up ? '調升' : '調降'}為${RATING_LABEL[c.rating_to]}`}
    >
      <span className={styles.main}>
        <span className={styles.name}>
          {name}
          {name !== c.instrument_code && <span className={`${atoms.code} ${styles.code}`}>{c.instrument_code}</span>}
        </span>
        <span className={styles.meta}>
          <span>{broker}</span>
          <span className={atoms.num}>{shortDate(c.report_date)}</span>
        </span>
      </span>
      <span className={styles.change}>
        <span className={styles.from}>{RATING_LABEL[c.rating_from]}</span>
        <ArrowRight
          size={12} strokeWidth={2.2} aria-hidden
          className={up ? styles.arrowUp : styles.arrowDown}
        />
        <span className={`${atoms.badge} ${atoms[c.rating_to]}`}>{RATING_LABEL[c.rating_to]}</span>
      </span>
    </Link>
  )
}

/** 首頁右上：最新評等變動（點一列到該標的的券商共識）。 */
export function RatingChangesTile({ items, failed, onRetry, className, style }: Props) {
  return (
    <section
      className={`${atoms.tile} ${styles.tile}${className ? ` ${className}` : ''}`}
      style={style}
      aria-labelledby="library-rating-changes"
    >
      <div className={styles.head}>
        <h2 id="library-rating-changes" className={atoms.h2}>最新評等變動</h2>
        <Link className={`${atoms.lk} ${styles.more}`} to="/consensus">
          券商共識<ChevronRight size={14} strokeWidth={1.8} aria-hidden />
        </Link>
      </div>
      {failed ? (
        <p className={styles.note}>
          評等變動載入失敗
          <button type="button" className={atoms.lk} onClick={onRetry}>重試</button>
        </p>
      ) : !items || items.length === 0 ? (
        <p className={styles.note}>目前沒有評等變動</p>
      ) : (
        items.map(c => <ChangeRow key={`${c.market}-${c.instrument_code}-${c.broker}-${c.report_date}`} c={c} />)
      )}
    </section>
  )
}
