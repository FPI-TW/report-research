import { MotionLink } from '../../components/primitives/MotionLink'
import { springHover } from '../../lib/motionTokens'
import type { SimilarReport } from '../../lib/readingSchemas'
import { matchedPct, matchedText, reportHref, similarMeta } from './readingFormat'
import styles from './SimilarReports.module.css'

interface Props {
  items: SimilarReport[]
}

/** 相似研報。空清單時由呼叫端整區不渲染。 */
export function SimilarReports({ items }: Props) {
  return (
    <section className={styles.sim} aria-labelledby="similar-heading">
      <div className={styles.head}>
        <h2 id="similar-heading" className={styles.title}>相似研報</h2>
        <span className={styles.note}>以全文切成 {items[0]?.total_probes ?? 0} 個語意段落比對，顯示重疊最高者</span>
      </div>
      <div className={styles.row}>
        {items.slice(0, 4).map(item => (
          <MotionLink
            key={item.file_hash}
            className={styles.card}
            to={reportHref(item.file_hash)}
            whileHover={{ y: -2 }}
            transition={springHover}
          >
            <div className={styles.cardTitle}>{item.file_name}</div>
            <div className={styles.cardMeta}>{similarMeta(item)}</div>
            <div className={styles.cardBar}>
              <span className={styles.bar}>
                <i
                  className={styles.barFill}
                  style={{ width: `${matchedPct(item.matched_probes, item.total_probes)}%` }}
                />
              </span>
              <span className={styles.pct}>{matchedText(item.matched_probes, item.total_probes)}</span>
            </div>
          </MotionLink>
        ))}
      </div>
    </section>
  )
}
