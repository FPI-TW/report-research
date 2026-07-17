import type { DimLabel, ThesisDimension } from '../../lib/radarSchemas'
import styles from './ThesisCompass.module.css'

const TONE: Record<DimLabel, string> = {
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
      {thesis.map(dim => {
        const n = dim.brokers_comparable
        const up = dim.brokers_strengthen
        const down = dim.brokers_weaken
        const mid = Math.max(0, n - up - down)
        const pct = (x: number) => (n > 0 ? (x / n) * 100 : 0)
        return (
          <article key={dim.dimension} className={styles.cell}>
            <div className={styles.head}>
              <span className={styles.name}>{dim.dimension_display}</span>
              <span className={`${styles.tag} ${TONE[dim.label]}`}>{dim.label_display}</span>
            </div>
            <div
              className={styles.split}
              role="img"
              aria-label={`${up} 家轉強、${down} 家轉弱，共 ${n} 家可比`}
            >
              {up > 0 ? <span className={styles.up} style={{ width: `${pct(up)}%` }} /> : null}
              {mid > 0 ? <span className={styles.mid} style={{ width: `${pct(mid)}%` }} /> : null}
              {down > 0 ? <span className={styles.down} style={{ width: `${pct(down)}%` }} /> : null}
            </div>
            <div className={styles.counts}>
              {n > 0
                ? `${up} 家轉強 · ${down} 家轉弱 · ${n} 家可比`
                : (dim.coverage_note || '資料不足')}
            </div>
          </article>
        )
      })}
    </section>
  )
}
