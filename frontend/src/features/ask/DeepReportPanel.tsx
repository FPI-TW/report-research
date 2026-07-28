import { useState } from 'react'
import { Callout } from '../../components/primitives/Callout'
import { Icon } from '../../components/primitives/Icon'
import { Reveal } from '../../components/primitives/Reveal'
import { RippleButton, RippleButtonRipples } from '../../components/animate-ui/primitives/buttons/ripple'
import type { ReportState } from '../../lib/askReducer'
import { TemplateSelector } from './TemplateSelector'
import { RerenderControl } from './RerenderControl'
import { ReportProgress } from './ReportProgress'
import styles from './DeepReportPanel.module.css'

function safeDownload(url: string | null): string | null {
  if (!url) return null
  if (url.startsWith('/') && !url.startsWith('//')) return url
  try { if (new URL(url).origin === location.origin) return url } catch { /* ignore */ }
  return null
}

interface Props {
  report: ReportState
  onGenerate: (templateId?: string) => void
  onDecline: () => void
  onCancel?: (runId: string) => void
}

export function DeepReportPanel({ report, onGenerate, onDecline, onCancel }: Props) {
  const [templateId, setTemplateId] = useState<string | undefined>(undefined)
  if (report.status === 'idle') return null

  if (report.status === 'offered') {
    // 版面改為「說明在上、版型縮圖成排、動作在最下」的直式流程。原本是把三者塞進同一列
    // flex：版型卡片被夾在文案與按鈕中間、寬度隨剩餘空間亂縮，是整個面板最醜的一塊。
    return (
      <div className={styles.offer}>
        <div className={styles.offerHead}>
          <span className={styles.offerIcon}><Icon name="fileText" size={18} /></span>
          <div className={styles.offerMain}>
            <div className={styles.offerTitle}>要不要整理成完整 PDF 深度研報？</div>
            <div className={styles.offerSub}>彙整本輪引用來源，逐節撰寫含 KPI 與圖表的深度研報，約需 5–12 分鐘。生成期間可以自由離開。</div>
          </div>
        </div>
        <TemplateSelector value={templateId} onChange={setTemplateId} />
        <div className={styles.offerBtns}>
          <RippleButton type="button" className={styles.yes} hoverScale={1.03} tapScale={0.96} onClick={() => onGenerate(templateId)}>
            生成研報
            <RippleButtonRipples color="rgba(255,255,255,0.6)" />
          </RippleButton>
          <button type="button" className={styles.no} onClick={onDecline}>暫時不用</button>
        </div>
      </div>
    )
  }

  if (report.status === 'generating') {
    return <ReportProgress report={report} onCancel={onCancel} />
  }

  if (report.status === 'done') {
    const href = safeDownload(report.downloadUrl)
    return (
      <Reveal className={styles.done}>
        <div className={styles.doneHead}><Icon name="check" size={18} className={styles.doneIcon} /><span className={styles.doneTitle}>深度研報已完成</span></div>
        {report.title && <div className={styles.doneMeta}>{report.title}</div>}
        {href && <a className={styles.dl} href={href} download><Icon name="fileText" size={16} /> 下載 PDF</a>}
        {/* 換皮重出（M9b）：零 LLM 換版型。後端完整且有測試，先前卻沒有任何 UI 能觸發
            —— 生產 report_rendition 長期 0 列。輸出語言不變（後端沿用產出當時的 locale）。*/}
        {report.reportId && <RerenderControl reportId={report.reportId} />}
      </Reveal>
    )
  }

  return <Callout variant="error" action={{ label: '重試', onClick: () => onGenerate(templateId) }}>{report.errorText || '研報生成失敗，請重試'}</Callout>
}
