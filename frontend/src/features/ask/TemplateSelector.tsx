import { useQuery } from '@tanstack/react-query'
import { getReportTemplates } from '../../lib/askApi'
import styles from './TemplateSelector.module.css'

interface Props {
  value: string | undefined       // undefined＝未選（後端用預設）
  onChange: (id: string) => void
}

/**
 * 研報渲染模板選擇器（M9b）。讀 /api/report-templates 呈現可選模板卡片。
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
              <span className={styles.name}>
                {t.name}
                {t.is_default && <span className={styles.badge}>預設</span>}
              </span>
              <span className={styles.desc}>{t.description}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
