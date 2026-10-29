import { useEffect, useRef, useState } from 'react'
import { Link, useHref } from 'react-router'
import { ArrowLeft, Check, File, Link as LinkIcon, MessageSquare } from 'lucide-react'
import { copyText } from '../../lib/clipboard'
import { displayTitle } from '../../lib/displayTitle'
import { marketLabel, marketTint, reportTypeLabel } from '../../lib/meta'
import { reportFileHref } from '../../lib/readingApi'
import type { ReadingDoc } from '../../lib/readingSchemas'
import { showToast } from '../../lib/toast'
import { fmtDate, reportHref } from './readingFormat'
import styles from './ReportHeader.module.css'

interface Props {
  doc: ReadingDoc
  /** 返回鈕的文字（來源頁名）與動作，見 backNav.ts */
  back: { label: string; go: () => void }
}

/** 複製後綠勾維持多久（交接包「互動」：按鈕圖示換成綠勾 1.5 秒）。 */
const COPIED_MS = 1500

/**
 * 研報面板的頁首：返回 → 市場／券商／日期／報告類型 → 標題 → 標的代號 → 動作列。
 * 不加分隔點：同一列的資訊靠間距區分（產品 UI 規則）。
 */
export function ReportHeader({ doc, back }: Props) {
  const market = doc.market ?? ''
  const broker = doc.source_display || doc.source || null
  const date = fmtDate(doc.report_date)
  const targets = [...doc.stock_targets, ...doc.futures_targets]
  const title = displayTitle(doc)
  // 分享出去的是乾淨的閱讀頁網址（含 basename），不帶舊網址殘留的 ?chunk／?view
  const shareHref = useHref(reportHref(doc.file_hash))

  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  const copyLink = () => {
    // 走 lib/clipboard：區網入口是 HTTP ＋ 私有 IP，navigator.clipboard 在那裡不存在。
    // 失敗一定要說，靜默假裝成功是最糟的結果。
    copyText(new URL(shareHref, window.location.origin).href).then(
      () => {
        setCopied(true)
        showToast('已複製連結', { ok: true })
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(() => setCopied(false), COPIED_MS)
      },
      () => showToast('複製失敗，請手動複製網址'),
    )
  }

  return (
    <header className={styles.hd}>
      <button type="button" className={styles.back} onClick={back.go}>
        <ArrowLeft size={14} strokeWidth={1.8} aria-hidden />
        {back.label}
      </button>

      <div className={styles.metas}>
        {market && (
          <span className={styles.mkt} style={marketTint(market)}>
            {marketLabel(market)}
          </span>
        )}
        {broker && <b className={styles.broker}>{broker}</b>}
        {date && <span className={styles.num}>{date}</span>}
        {/* report_type 是獨有的報告分類（個股報告/產業報告…），有值才顯示（約 80% 空）；
            少數英文碼（snapshot、memo）照 lib/meta 的對照轉中文 */}
        {doc.report_type && <span>{reportTypeLabel(doc.report_type)}</span>}
      </div>

      <h1 className={styles.title}>{title}</h1>

      {targets.length > 0 && (
        <div className={styles.codes}>
          {/* 同一代號可能同時是 stock 與 futures 標的，併陣列後 key 會撞號 → 補 index */}
          {targets.map((code, i) => (
            <span key={`${code}-${i}`} className={styles.code}>{code}</span>
          ))}
        </div>
      )}

      <div className={styles.acts}>
        {/* 帶報告名去研究助理預填題目（?q=），不自動送出、也不縮限檢索範圍 ——
            aria-label/title 據實說明，別讓人以為只會讀這一篇。
            真正的單篇 scope 要動到問答主路徑（store._meta_filters 加 file_hash →
            hybrid_search 參數 → /api/ask filters → answer prompt），另案處理。 */}
        <Link
          className={`${styles.btn} ${styles.btnPrimary}`}
          to={`/assistant?q=${encodeURIComponent(`關於《${title}》：`)}`}
          aria-label="就這篇提問：帶著這篇的標題到研究助理預填題目（檢索仍涵蓋全語料）"
          title="帶著這篇的標題到研究助理預填題目；檢索範圍仍是全語料"
        >
          <MessageSquare size={15} strokeWidth={1.8} aria-hidden />
          就這篇提問
        </Link>
        {/* 原始檔是後端端點、不是 SPA 路由：必須用原生 <a>。
            react-router 的 Link 會套上 router 的 basename（/app），
            變成 /app/api/report/... 而 404。

            target 依格式分流：PDF 由後端以 inline 提供（web/routers/report_file.py），
            新分頁能直接渲染；**.docx 是 attachment**，配 target="_blank" 只會開一個
            下載完就空著的分頁 —— 那些改走 download 屬性，不開新分頁。 */}
        {doc.has_file && (
          <a
            className={styles.btn}
            href={reportFileHref(doc.report_id)}
            title={doc.file_name}
            {...(doc.is_pdf
              ? { target: '_blank', rel: 'noopener noreferrer' }
              : { download: true })}
          >
            <File size={15} strokeWidth={1.6} aria-hidden />
            {/* 非 PDF（.docx）不能標成「原始 PDF」：那些研報右欄走的是文字後備，
                文案已叫讀者「由頁首下載原始檔」，鈕上卻寫 PDF 會對不上。 */}
            {doc.is_pdf ? '原始 PDF' : '原始檔'}
          </a>
        )}
        <button
          type="button"
          className={`${styles.btn} ${styles.iconBtn}`}
          aria-label={copied ? '已複製連結' : '複製連結'}
          title={copied ? '已複製連結' : '複製連結'}
          onClick={copyLink}
        >
          {copied
            ? <Check key="done" className={styles.copied} size={15} strokeWidth={2.2} aria-hidden />
            : <LinkIcon key="idle" size={15} strokeWidth={1.8} aria-hidden />}
        </button>
      </div>
    </header>
  )
}
