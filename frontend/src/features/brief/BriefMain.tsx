import { useMemo } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { renderAnswer } from '../../lib/askMarkdown'
import type { BriefPayload } from '../../lib/briefSchemas'
import { marketColor } from '../../lib/meta'
import { OTHER_COLOR, briefMix, parseBriefMarkdown, type BriefFocusBlock, type BriefHighlight, type BriefMixItem } from './briefContent'
import { dayTitle, yearWeekday } from './briefDates'
import { InlineText } from './InlineText'
import styles from './BriefPage.module.css'

interface Props {
  brief: BriefPayload
  older: string | null
  newer: string | null
  onGo: (iso: string) => void
}

/** 左欄：概況卡、今日重點、市場焦點（外層 BriefPage 負責 stagger／切換日期重播）。 */
export function BriefMain({ brief, older, newer, onGo }: Props) {
  const content = useMemo(() => parseBriefMarkdown(brief.markdown, brief.reports), [brief.markdown, brief.reports])
  const mix = useMemo(() => briefMix(brief.markets), [brief.markets])

  return (
    <>
      <Overview brief={brief} mix={mix} older={older} newer={newer} onGo={onGo} />
      {content.highlights.length > 0 && <Highlights items={content.highlights} />}
      {content.focus.length > 0 && <Focus blocks={content.focus} mix={mix} />}
      {content.extras.map(x => (
        <section key={x.title} className={`${styles.tile} ${styles.mdTile}`} aria-label={x.title}>
          <h2 className={styles.cardTitle}>{x.title}</h2>
          {/* 簡報沒有 [n] 引用，sourceCount 給 0：超出範圍的 [n] 原樣當文字留著。 */}
          <div className={styles.md}>{renderAnswer(x.body, 0, () => {})}</div>
        </section>
      ))}
      {content.fallback && (
        <section className={`${styles.tile} ${styles.mdTile}`} aria-label="簡報內容">
          <div className={styles.md}>{renderAnswer(content.fallback, 0, () => {})}</div>
        </section>
      )}
    </>
  )
}

function Overview({ brief, mix, older, newer, onGo }: { brief: BriefPayload; mix: BriefMixItem[] } & Omit<Props, 'brief'>) {
  const marketCount = mix.filter(m => m.market !== null).length
  const mixLabel = `新進研報市場分布：${mix.map(m => `${m.label} ${m.count} 篇`).join('，')}`
  return (
    <section className={`${styles.tile} ${styles.overview}`} aria-label="簡報概況">
      <div className={styles.ovHead}>
        <div className={styles.ovDate}>
          <h2 className={styles.ovTitle}>{dayTitle(brief.brief_date)}</h2>
          <span className={styles.ovSub}>{yearWeekday(brief.brief_date)}</span>
        </div>
        <button type="button" className={styles.arr} aria-label="前一份簡報" disabled={!older} onClick={() => older && onGo(older)}>
          <ChevronLeft size={16} strokeWidth={2} aria-hidden="true" />
        </button>
        <button type="button" className={styles.arr} aria-label="後一份簡報" disabled={!newer} onClick={() => newer && onGo(newer)}>
          <ChevronRight size={16} strokeWidth={2} aria-hidden="true" />
        </button>
      </div>
      <dl className={styles.stats}>
        <div className={styles.stat}><dt>篇新進研報</dt><dd>{brief.report_count}</dd></div>
        {/* 市場數與券商數是後端讀取時現算的派生資料，缺值（查詢降級）就只顯示篇數。 */}
        {brief.markets && <div className={styles.stat}><dt>個市場</dt><dd>{marketCount}</dd></div>}
        {brief.broker_count != null && <div className={styles.stat}><dt>家券商</dt><dd>{brief.broker_count}</dd></div>}
      </dl>
      {mix.length > 0 && (
        <>
          <div className={styles.mixBar} role="img" aria-label={mixLabel}>
            {mix.map(m => (
              <span key={m.label} className={styles.mixSeg} style={{ flex: `${m.count} 1 0`, background: m.color }} />
            ))}
          </div>
          <ul className={styles.legend} aria-hidden="true">
            {mix.map(m => (
              <li key={m.label} className={styles.legendItem}>
                <span className={styles.swatch} style={{ background: m.color }} />
                {m.label}
                <span className={styles.legendCount}>{m.count}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

function number(i: number): string {
  return i < 9 ? `0${i + 1}` : String(i + 1)
}

function Highlights({ items }: { items: BriefHighlight[] }) {
  return (
    <section className={`${styles.tile} ${styles.highlights}`} aria-labelledby="brief-hl">
      <h2 id="brief-hl" className={styles.cardTitle}>今日重點</h2>
      {/* list-style 拿掉後 Safari 會丟掉清單語意，role 補回來。編號由 <ol> 提供，畫面上的編號只是裝飾。 */}
      <ol className={styles.hlList} role="list">
        {items.map((h, i) => (
          <li key={i} className={i === 0 ? `${styles.hl} ${styles.hlLead}` : styles.hl}>
            <span className={styles.hlNo} aria-hidden="true">{number(i)}</span>
            <span className={styles.hlBody}>
              <p className={styles.hlText}><InlineText text={h.text} /></p>
              {h.brokers.length > 0 && <span className={styles.hlBroker}>{h.brokers.join('、')}</span>}
            </span>
          </li>
        ))}
      </ol>
    </section>
  )
}

function Focus({ blocks, mix }: { blocks: BriefFocusBlock[]; mix: BriefMixItem[] }) {
  const counts = new Map(mix.filter(m => m.market).map(m => [m.market as string, m.count]))
  const max = Math.max(1, ...mix.map(m => m.count))
  return (
    <section className={`${styles.tile} ${styles.focus}`} aria-labelledby="brief-focus">
      <h2 id="brief-focus" className={styles.cardTitle}>市場焦點</h2>
      <div className={styles.focusGrid}>
        {blocks.map((b, i) => {
          const count = b.market ? counts.get(b.market) : undefined
          const color = b.market ? marketColor(b.market) : OTHER_COLOR
          return (
            <div key={`${b.label ?? ''}-${i}`}>
              {b.label && (
                <div className={styles.focusHead}>
                  <span className={styles.focusDot} style={{ background: color }} aria-hidden="true" />
                  <h3 className={styles.focusName}>{b.label}</h3>
                  {count != null && <span className={styles.focusCount}>{count} 篇</span>}
                </div>
              )}
              {count != null && (
                <span className={styles.share} aria-hidden="true">
                  <span className={styles.shareFill} style={{ width: `${Math.round((count / max) * 100)}%`, background: color }} />
                </span>
              )}
              {b.paragraphs.map((p, j) => (
                <p key={j} className={styles.focusText}><InlineText text={p} /></p>
              ))}
            </div>
          )
        })}
      </div>
    </section>
  )
}
