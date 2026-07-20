import { Link } from 'react-router'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import { MotionLink } from '../../components/primitives/MotionLink'
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
  const targets = [...doc.stock_targets, ...doc.futures_targets]
  const types = doc.instrument_types.map(instrumentLabel)
  const meta = metaParts(doc)

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
          <h1 className={styles.title}>{doc.file_name}</h1>
          <div className={styles.metas}>
            {market && (
              <span className={styles.mkt} style={marketTint(market)}>
                {marketLabel(market)}
              </span>
            )}
            {meta.map((part, i) => (
              <span key={part} className={styles.metaItem}>
                {i > 0 && <span className={styles.dot} aria-hidden="true">·</span>}
                {i === 0 ? <b>{part}</b> : part}
              </span>
            ))}
            {targets.map(code => (
              <span key={code} className={styles.code}>{code}</span>
            ))}
            {types.map(t => (
              <span key={t} className={styles.tag}>{t}</span>
            ))}
          </div>
        </div>

        <div className={styles.acts}>
          {/* 原始檔是後端端點、不是 SPA 路由：必須用原生 <a>。
              react-router 的 Link 會套上 router 的 basename（/app），
              變成 /app/api/report/... 而 404。 */}
          {doc.has_file && (
            <Pressable
              as="a"
              className={styles.btn}
              href={reportFileHref(doc.report_id)}
              target="_blank"
              rel="noopener noreferrer"
              hoverScale={1.02}
              tapScale={0.96}
            >
              <Icon name="fileText" size={15} />
              原始 PDF
            </Pressable>
          )}
          {/* 帶報告名去問答頁預填題目（?q=），不自動送出、也不縮限檢索範圍 ——
              aria-label/title 據實說明，別讓人以為只會讀這一篇。
              真正的單篇 scope 要動到問答主路徑（store._meta_filters 加 file_hash →
              hybrid_search 參數 → /api/ask filters → answer prompt），另案處理。 */}
          <MotionLink
            className={`${styles.btn} ${styles.btnGold}`}
            to={`/ask?q=${encodeURIComponent(`關於《${doc.file_name}》：`)}`}
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
