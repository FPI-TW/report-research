import { Callout } from '../../components/primitives/Callout'
import { Icon } from '../../components/primitives/Icon'
import type { ReportState } from '../../lib/askReducer'
import styles from './DeepReportPanel.module.css'

function safeDownload(url: string | null): string | null {
  if (!url) return null
  if (url.startsWith('/') && !url.startsWith('//')) return url
  try { if (new URL(url).origin === location.origin) return url } catch { /* ignore */ }
  return null
}

interface Props { report: ReportState; onGenerate: () => void; onDecline: () => void }

export function DeepReportPanel({ report, onGenerate, onDecline }: Props) {
  if (report.status === 'idle') return null

  if (report.status === 'offered') {
    return (
      <div className={`${styles.offer} tf-reveal`}>
        <span className={styles.offerIcon}><Icon name="fileText" size={18} /></span>
        <div className={styles.offerMain}>
          <div className={styles.offerTitle}>要不要整理成完整 PDF 深度研報？</div>
          <div className={styles.offerSub}>彙整本輪引用來源，生成含 KPI 與圖表的深度研報，約需 3–5 分鐘。</div>
        </div>
        <div className={styles.offerBtns}>
          <button type="button" className={styles.yes} onClick={onGenerate}>生成研報</button>
          <button type="button" className={styles.no} onClick={onDecline}>暫時不用</button>
        </div>
      </div>
    )
  }

  if (report.status === 'generating') {
    return (
      <div className={styles.gen}>
        <div className={styles.genTitle}>深度研報生成中…</div>
        <div className={styles.track}>
          <div className={`${styles.fill} ${report.pct === 50 ? styles.indet : ''}`} style={{ width: `${report.pct}%` }} />
        </div>
        <div className={styles.genMeta}>{report.stageText}</div>
      </div>
    )
  }

  if (report.status === 'done') {
    const href = safeDownload(report.downloadUrl)
    return (
      <div className={`${styles.done} tf-reveal`}>
        <div className={styles.doneHead}><Icon name="check" size={18} className={styles.doneIcon} /><span className={styles.doneTitle}>深度研報已完成</span></div>
        {report.title && <div className={styles.doneMeta}>{report.title}</div>}
        {href && <a className={styles.dl} href={href} download><Icon name="fileText" size={16} /> 下載 PDF</a>}
      </div>
    )
  }

  return <Callout variant="error" action={{ label: '重試', onClick: onGenerate }}>{report.errorText || '研報生成失敗，請重試'}</Callout>
}
