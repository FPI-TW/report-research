import { useQuery } from '@tanstack/react-query'
import { getReportTemplates } from '../../lib/askApi'
import { Icon } from '../../components/primitives/Icon'
import styles from './TemplateSelector.module.css'

interface Props {
  value: string | undefined       // undefined＝未選（後端用預設）
  onChange: (id: string) => void
}

/**
 * 三款模板各有明確的版面個性（雙欄密集／單欄留白／深色鎏金），純文字卡片把這件事完全
 * 藏起來了——使用者要在「國際投行經典」和「現代簡潔」之間選，卻看不到任何一頁長什麼樣。
 * 這裡用 CSS 畫出各自的版面骨架當縮圖：零圖檔、零請求，且改模板樣式時一眼看得出對不對。
 *
 * 骨架以 `data-preview={id}` 對應樣式；registry 之後新增模板時，沒有對應樣式的 id
 * 會落到通用骨架（`.previewGeneric`），不會破版也不會擋住選擇。
 */
const PREVIEW_CLASS: Record<string, string> = {
  'ib-classic': styles.previewClassic,
  'broker-modern': styles.previewModern,
  'privatebank-dark': styles.previewDark,
}

function Preview({ id }: { id: string }) {
  const variant = PREVIEW_CLASS[id] ?? styles.previewGeneric
  return (
    <span className={`${styles.preview} ${variant}`} aria-hidden="true">
      <span className={styles.pvHeader} />
      <span className={styles.pvTitle} />
      <span className={styles.pvKpi}>
        <span /><span /><span />
      </span>
      <span className={styles.pvBody}>
        <span className={styles.pvCol} />
        <span className={styles.pvCol} />
      </span>
    </span>
  )
}

/**
 * 研報渲染模板選擇器（M9b）。讀 /api/report-templates 呈現可選模板。
 * fail-open：載入中／失敗／清單為空時整區不渲染——不擋研報生成（後端未帶 template_id
 * 走預設）。
 */
export function TemplateSelector({ value, onChange }: Props) {
  const { data: templates } = useQuery({
    queryKey: ['report-templates'],
    queryFn: getReportTemplates,
    staleTime: 5 * 60 * 1000,
  })
  if (!templates || templates.length === 0) return null

  return (
    <div className={styles.wrap}>
      <div className={styles.label}>選擇版型</div>
      <div className={styles.grid} role="radiogroup" aria-label="研報版型">
        {templates.map((t) => {
          const selected = value === t.id || (value === undefined && t.is_default)
          return (
            <button
              key={t.id}
              type="button"
              role="radio"
              aria-checked={selected}
              className={selected ? `${styles.card} ${styles.selected}` : styles.card}
              onClick={() => onChange(t.id)}
            >
              <Preview id={t.id} />
              <span className={styles.meta}>
                <span className={styles.name}>
                  {t.name}
                  {t.is_default && <span className={styles.badge}>預設</span>}
                </span>
                <span className={styles.desc}>{t.description}</span>
              </span>
              <span className={styles.tick}><Icon name="check" size={12} /></span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
