import { Link } from 'react-router'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import { MotionLink } from '../../components/primitives/MotionLink'
import { displayTitle } from '../../lib/displayTitle'
import { marketLabel, marketTint, instrumentLabel } from '../../lib/meta'
import { reportFileHref } from '../../lib/readingApi'
import type { ReadingDoc } from '../../lib/readingSchemas'
import { metaParts } from './readingFormat'
import styles from './ReportHeader.module.css'

interface Props {
  doc: ReadingDoc
}

/** 報頭：報告名（serif）+ 市場 chip + 券商 · 日期 · 標的代號 + 動作。 */
export function ReportHeader({ doc }: Props) {
  const market = doc.market ?? ''
  const broker = doc.source_display || doc.source || null
  const targets = [...doc.stock_targets, ...doc.futures_targets]
  const meta = metaParts(doc)
  const title = displayTitle(doc)

  return (
    <header className={styles.hd}>
      <nav className={styles.crumb} aria-label="麵包屑">
        <Link to="/search">檢索</Link>
        <span aria-hidden="true">›</span>
        <span>研報</span>
      </nav>

      <div className={styles.row}>
        <div className={styles.main}>
          {/* 頁標題用 h1（沿用 RadarHeader/HelpPage 慣例；AppShell 的 h1 為 srOnly 品牌名） */}
          <h1 className={styles.title}>{title}</h1>
          <div className={styles.metas}>
            {market && (
              <span className={styles.mkt} style={marketTint(market)}>
                {marketLabel(market)}
              </span>
            )}
            {meta.map((part, i) => (
              <span key={part} className={styles.metaItem}>
                {i > 0 && <span className={styles.dot} aria-hidden="true">·</span>}
                {/* 只把券商粗體：缺券商時 metaParts 會讓日期落在 index 0，沿用 i===0
                    會把日期當券商名粗體。改以「這個 token 是不是券商」判斷。 */}
                {part === broker ? <b>{part}</b> : part}
              </span>
            ))}
            {/* 同一代號可能同時是 stock 與 futures 標的，併陣列後 key 會撞號 → 補 index */}
            {targets.map((code, i) => (
              <span key={`${code}-${i}`} className={styles.code}>{code}</span>
            ))}
            {/* 兩個 code 可能映到同一顯示標籤 → 以原始 code+index 當 key，別用顯示字串 */}
            {doc.instrument_types.map((code, i) => (
              <span key={`${code}-${i}`} className={styles.tag}>{instrumentLabel(code)}</span>
            ))}
            {/* report_type 是獨有的報告分類（個股報告/產業報告…），有值才顯示（約 80% 空） */}
            {doc.report_type && <span className={styles.tag}>{doc.report_type}</span>}
          </div>
        </div>

        <div className={styles.acts}>
          {/* 原始檔是後端端點、不是 SPA 路由：必須用原生 <a>。
              react-router 的 Link 會套上 router 的 basename（/app），
              變成 /app/api/report/... 而 404。

              target 依格式分流：PDF 由後端以 inline 提供（web/routers/report_file.py），
              新分頁能直接渲染；**.docx 是 attachment**，配 target="_blank" 只會開一個
              下載完就空著的分頁 —— 那些改走 download 屬性，不開新分頁。 */}
          {doc.has_file && (
            <Pressable
              as="a"
              className={styles.btn}
              href={reportFileHref(doc.report_id)}
              {...(doc.is_pdf
                ? { target: '_blank', rel: 'noopener noreferrer' }
                : { download: true })}
              hoverScale={1.02}
              tapScale={0.96}
            >
              <Icon name="fileText" size={15} />
              {/* 非 PDF（.docx）不能標成「原始 PDF」：那些研報右欄走的是文字後備，
                  文案已叫讀者「由頁首下載原始檔」，鈕上卻寫 PDF 會對不上。 */}
              {doc.is_pdf ? '原始 PDF' : '原始檔'}
            </Pressable>
          )}
          {/* 帶報告名去問答頁預填題目（?q=），不自動送出、也不縮限檢索範圍 ——
              aria-label/title 據實說明，別讓人以為只會讀這一篇。
              真正的單篇 scope 要動到問答主路徑（store._meta_filters 加 file_hash →
              hybrid_search 參數 → /api/ask filters → answer prompt），另案處理。 */}
          <MotionLink
            className={`${styles.btn} ${styles.btnGold}`}
            to={`/ask?q=${encodeURIComponent(`關於《${title}》：`)}`}
            aria-label="就這篇提問：帶著這篇的標題到問答頁預填題目（檢索仍涵蓋全語料）"
            title="帶著這篇的標題到問答頁預填題目；問答的檢索範圍仍是全語料"
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.96 }}
          >
            <Icon name="messages" size={15} />
            就這篇提問
          </MotionLink>
        </div>
      </div>
    </header>
  )
}
