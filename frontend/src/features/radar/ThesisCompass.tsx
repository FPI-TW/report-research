import { Icon } from '../../components/primitives/Icon'
import type { DimLabel, ThesisDimension } from '../../lib/radarSchemas'
import styles from './ThesisCompass.module.css'

const LABEL_ICON: Record<DimLabel, 'trendUp' | 'trendDown' | 'diverge' | 'trendFlat' | 'notComparable'> = {
  strengthen: 'trendUp',
  weaken: 'trendDown',
  diverging: 'diverge',
  stable: 'trendFlat',
  insufficient: 'notComparable',
}

const LABEL_TONE: Record<DimLabel, string> = {
  strengthen: styles.strengthen,
  weaken: styles.weaken,
  diverging: styles.diverging,
  stable: styles.stable,
  insufficient: styles.insufficient,
}

interface Props {
  thesis: ThesisDimension[]
}

export function ThesisCompass({ thesis }: Props) {
  return (
    <section className={styles.grid} aria-label="四向觀點">
      {thesis.map(dim => (
        <article key={dim.dimension} className={styles.cell}>
          <div className={styles.dim}>{dim.dimension_display}</div>
          <div className={`${styles.status} ${LABEL_TONE[dim.label]}`}>
            <Icon name={LABEL_ICON[dim.label]} size={18} className={styles.icon} />
            <span>{dim.label_display}</span>
          </div>
          <div className={styles.note}>
            {dim.coverage_note
              || (dim.brokers_comparable > 0
                ? `${dim.brokers_strengthen} 家轉強 · ${dim.brokers_weaken} 家轉弱 · ${dim.brokers_comparable} 家可比`
                : '資料不足')}
          </div>
          {dim.sample_summary ? (
            <div className={styles.summary}>{dim.sample_summary}</div>
          ) : null}
        </article>
      ))}
    </section>
  )
}
