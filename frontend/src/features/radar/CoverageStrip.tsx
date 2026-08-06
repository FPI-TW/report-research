import { useId, useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import type { Coverage } from '../../lib/radarSchemas'
import styles from './CoverageStrip.module.css'

interface Props {
  coverage: Coverage
}

/**
 * 資料涵蓋橫列：取代原本「資料品質」那張 KPI 卡。
 *
 * 三個數字在同一句話裡才讀得懂彼此的關係（追蹤 ⊇ 已擷取 ⊇ 納入共識），拆成
 * 「6 / 14 家」＋一條進度條時，讀者拿到的是一個比例卻不知道分母是什麼。
 * 進度條與 `coverage.note` 沒有刪掉，收進展開層——它們是「為什麼只有這些」的解釋，
 * 不是每次都要看的數字。
 */
export function CoverageStrip({ coverage }: Props) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const pct = coverage.brokers_total
    ? Math.round((coverage.brokers_extracted / coverage.brokers_total) * 100)
    : 0

  return (
    <section className={styles.strip} aria-label="資料涵蓋">
      <div className={styles.head}>
        <p className={styles.counts}>
          <b>{coverage.brokers_total}</b> 家追蹤
          <span className={styles.midDot}>・</span>
          <b>{coverage.brokers_extracted}</b> 家已擷取
          <span className={styles.midDot}>・</span>
          <b>{coverage.brokers_in_consensus}</b> 家納入共識
        </p>
        <Pressable
          type="button"
          className={styles.toggle}
          aria-expanded={open}
          aria-controls={panelId}
          hoverScale={1}
          onClick={() => setOpen(o => !o)}
        >
          查看資料涵蓋說明
          <Icon
            name="chevronDown"
            size={16}
            className={`${styles.chev} ${open ? styles.chevOpen : ''}`}
          />
        </Pressable>
      </div>

      {open ? (
        <div className={styles.panel} id={panelId}>
          <dl className={styles.facts}>
            <div>
              <dt>已擷取比例</dt>
              <dd>
                <span className={styles.bar}><i style={{ width: `${pct}%` }} /></span>
                <span className={styles.pct}>
                  {coverage.brokers_extracted} / {coverage.brokers_total || '—'} 家
                </span>
              </dd>
            </div>
            <div>
              <dt>可用研報</dt>
              <dd>{coverage.reports_available} 份</dd>
            </div>
          </dl>
          {coverage.state === 'partial' ? (
            <p className={styles.partial}>部分資料 · 非完整品質</p>
          ) : null}
          {coverage.note ? <p className={styles.note}>{coverage.note}</p> : null}
          <p className={styles.note}>
            「納入共識」只計窗期內有有效訊號的券商；同一家券商僅取最新一份研報，不累加舊報告。
          </p>
        </div>
      ) : null}
    </section>
  )
}
