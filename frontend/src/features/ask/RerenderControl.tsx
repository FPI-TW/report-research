import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getReportTemplates, rerenderReport } from '../../lib/askApi'
import { Callout } from '../../components/primitives/Callout'
import styles from './RerenderControl.module.css'

interface Props {
  reportId: string
}

/**
 * 換皮重出入口（M9b）。研報完成後可換另一套版型重出 PDF——**零 LLM、不重新生成內容**。
 *
 * 後端 `POST /api/report-doc/{id}/rerender` 早已完整且有測試，但先前沒有任何 UI 能
 * 觸發它：生產 `report_rendition` 長期 0 列、`current_rendition_id` 0/13。
 *
 * 輸出語言刻意不在此提供選項：換皮只換版型。後端一律沿用**產出當時**存下的 locale，
 * 否則英文研報換皮後會變成「英文內文 + 中文封面/頁首/免責」。
 *
 * fail-open：模板清單載入中／失敗／為空時整區不渲染——換皮是加分功能，不該擋住下載。
 */
export function RerenderControl({ reportId }: Props) {
  const { data: templates } = useQuery({
    queryKey: ['report-templates'],
    queryFn: getReportTemplates,
    staleTime: 5 * 60 * 1000,
  })
  const [busy, setBusy] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  if (!templates || templates.length === 0) return null

  async function apply(id: string) {
    setBusy(id)
    setFailed(false)
    try {
      await rerenderReport(reportId, id)
      setDone(id)
    } catch {
      // 後端失敗時會保留上一個可下載版本，所以這裡只需告知、不需回滾
      setFailed(true)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.label}>換一套版型（不重新生成內容）</div>
      <div className={styles.row} role="group" aria-label="換研報版型">
        {templates.map((t) => (
          <button
            key={t.id}
            type="button"
            className={done === t.id ? `${styles.btn} ${styles.btnDone}` : styles.btn}
            disabled={busy !== null}
            onClick={() => apply(t.id)}
          >
            {busy === t.id ? '重出中…' : t.name}
          </button>
        ))}
      </div>
      {done && (
        <div className={styles.hint}>
          已換為「{templates.find((t) => t.id === done)?.name ?? done}」——重新點上方下載
          即取得新版型 PDF。
        </div>
      )}
      {failed && (
        <Callout variant="error">換版型失敗，原本的 PDF 仍可下載。</Callout>
      )}
    </div>
  )
}
