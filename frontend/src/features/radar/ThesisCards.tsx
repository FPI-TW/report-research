import { useId } from 'react'
import type { DimKey, ThesisDimension } from '../../lib/radarSchemas'
import shared from './shared.module.css'
import styles from './ThesisCards.module.css'

/** 四向的閱讀順序固定；後端保證四格齊全，但陣列順序不是 API 契約。 */
const ORDER: DimKey[] = ['outlook', 'catalyst', 'risk', 'valuation']

/**
 * 四向觀點四卡（交接包：強化紅、轉弱綠、分歧斜紋、持平灰）。
 *
 * 維度名與結論字樣用後端的 `dimension_display`／`label_display`：那是這組詞彙唯一的定義處
 * （`radar/scale.py`），前端另寫一份遲早會和後端說法不同。
 */
export function ThesisCards({ thesis }: { thesis: ThesisDimension[] }) {
  const id = useId()
  const ordered = [...thesis].sort((a, b) => ORDER.indexOf(a.dimension) - ORDER.indexOf(b.dimension))
  return (
    <section className={styles.section} aria-labelledby={id}>
      <h2 id={id} className={styles.heading}>四向觀點</h2>
      <div className={styles.grid}>
        {ordered.map(dim => (
          <article key={dim.dimension} className={`${shared.tile} ${styles.card}`}>
            <div className={styles.head}>
              <h3 className={styles.dim}>{dim.dimension_display}</h3>
              <span className={`${styles.badge} ${styles[dim.label]}`}>{dim.label_display}</span>
            </div>
            <div className={styles.counts}>
              <span>轉強 <b className={shared.num}>{dim.brokers_strengthen}</b></span>
              <span>轉弱 <b className={shared.num}>{dim.brokers_weaken}</b></span>
              <span>可比 <b className={shared.num}>{dim.brokers_comparable}</b></span>
            </div>
            {dim.sample_summary ? <p className={styles.summary}>{dim.sample_summary}</p> : null}
          </article>
        ))}
      </div>
    </section>
  )
}
