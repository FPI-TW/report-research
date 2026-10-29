import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import {
  ArrowRight, Check, ChevronDown, ChevronLeft, ChevronRight, Copy, Minus, PanelLeft, Plus, RotateCcw, Search,
  ThumbsDown, ThumbsUp,
} from 'lucide-react'
import { marketColor, marketTint } from '../../lib/meta'
import styles from './HelpDemos.module.css'

/** 示範畫面的設計寬度。座標都以這個寬度寫死（游標要準確點到目標），窄於它就整塊等比縮小。 */
const STAGE_W = 640

/**
 * 操作示範的外框（交接包「使用說明」）：9 秒循環、模擬游標點擊、結尾淡出。
 *
 * - 滑進畫面才播，而且每次進來都從頭播（`key` 換掉整段，CSS 動畫重新開始）；離開畫面就暫停。
 * - 減少動態時 CSS 直接停用動畫，各元素的基本樣式就是最終畫面（見 HelpDemos.module.css 檔頭）。
 * - 整塊是一張圖（role="img"），內容只是示意，對輔助技術隱藏。
 */
function Demo({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  // 沒有 IntersectionObserver（jsdom、極舊瀏覽器）就直接播。
  const [playing, setPlaying] = useState(() => typeof IntersectionObserver === 'undefined')
  const [run, setRun] = useState(0)

  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    // 觀察開始時會先回呼一次，初始尺寸也從這裡來。
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth
      if (w) setScale(Math.min(1, w / STAGE_W))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setPlaying(true)
        setRun(r => r + 1)
      } else {
        setPlaying(false)
      }
    }, { threshold: 0.5 })
    io.observe(el)
    return () => io.disconnect()
  }, [])

  return (
    <div
      ref={ref}
      className={styles.demo}
      role="img"
      aria-label={label}
      data-playing={playing || undefined}
      style={{ '--demo-scale': scale } as CSSProperties}
    >
      <div className={styles.stage} aria-hidden="true">
        <div key={run} className={styles.loop}>{children}</div>
      </div>
    </div>
  )
}

function Cursor({ className }: { className: string }) {
  return (
    <svg className={`${styles.cursor} ${className}`} viewBox="0 0 24 24">
      <path d="M5 3l14 8-6 2-3 6z" />
    </svg>
  )
}

function Mkt({ code, label }: { code: string; label: string }) {
  return <span className={styles.mkt} style={marketTint(code)}>{label}</span>
}

function Dist({ parts }: { parts: [string, number][] }) {
  return (
    <span className={styles.dist}>
      {parts.map(([cls, n]) => <span key={cls} className={styles[cls]} style={{ flexGrow: n }} />)}
    </span>
  )
}

/** 研報庫：輸入關鍵字按 Enter，結果出現後點「台股」篩選，美股那篇淡出。 */
export function LibraryDemo() {
  return (
    <Demo label="操作示範：在研報庫輸入關鍵字後按 Enter 搜尋，再點台股篩選結果">
      <div className={`${styles.card} ${styles.lSearch}`}>
        <Search size={14} strokeWidth={2} className={styles.faint} />
        <span className={styles.lType}>AI 伺服器散熱</span>
        <span className={styles.grow} />
        <span className={styles.lEnter}>Enter</span>
      </div>
      <div className={styles.lChips}>
        <span className={`${styles.chip} ${styles.lAll}`}>全部<span className={styles.chipN}>24</span></span>
        <span className={`${styles.chip} ${styles.lTw}`}>台股<span className={styles.chipN}>15</span></span>
        <span className={styles.chip}>美股<span className={styles.chipN}>9</span></span>
      </div>
      <div className={`${styles.card} ${styles.lRow} ${styles.lR1}`}>
        <span className={styles.lMkt} style={{ color: marketColor('TW') }}>台股</span>
        <span className={styles.lTitle}>伺服器散熱：液冷滲透率加速</span>
        <span className={styles.top}>最相關</span>
        <span className={`${styles.line} ${styles.markLine}`} style={{ maxWidth: 80 }} />
        <span className={styles.lMeta}>元大投顧{'\u3000'}10-27</span>
      </div>
      <div className={`${styles.card} ${styles.lRow} ${styles.lR2}`}>
        <span className={styles.lMkt} style={{ color: marketColor('TW') }}>台股</span>
        <span className={styles.lTitle}>散熱模組廠：AI 機櫃出貨上修</span>
        <span className={`${styles.line} ${styles.markLine}`} style={{ maxWidth: 60 }} />
        <span className={styles.lMeta}>凱基投顧{'\u3000'}10-24</span>
      </div>
      <div className={`${styles.card} ${styles.lRow} ${styles.lR3}`}>
        <span className={styles.lMkt} style={{ color: marketColor('US') }}>美股</span>
        <span className={styles.lTitle}>資料中心電力與散熱需求更新</span>
        <span className={`${styles.line} ${styles.markLine}`} style={{ maxWidth: 70 }} />
        <span className={styles.lMeta}>摩根大通{'\u3000'}10-22</span>
      </div>
      <Cursor className={styles.lCur} />
    </Demo>
  )
}

/** 閱讀頁：點第 2 條重點摘錄 → PDF 捲到第 2 頁並標出原文 → 在 PDF 上拖曳選字 → 工具列的複製鈕。 */
export function ReaderDemo() {
  return (
    <Demo label="操作示範：點重點摘錄，PDF 捲到原文並標出位置；再拖曳選取一行文字，按工具列的複製鈕">
      <div className={`${styles.card} ${styles.rPanel}`}>
        <span className={`${styles.line} ${styles.lineDark} ${styles.rTitle}`} />
        <span className={`${styles.line} ${styles.rMeta1}`} />
        <span className={`${styles.line} ${styles.rMeta2}`} />
        <span className={`${styles.rH} ${styles.rH1}`}>重點摘錄</span>
        <span className={`${styles.rTk} ${styles.rTk1}`}>
          <span className={styles.rNo}>1</span>
          <span className={styles.rBody}><span className={styles.line} /><span className={styles.line} style={{ width: '64%' }} /></span>
        </span>
        <span className={`${styles.rTk} ${styles.rTk2} ${styles.rTkOn}`}>
          <span className={`${styles.rNo} ${styles.rNoOn}`}>2</span>
          <span className={styles.rBody}>
            <span className={styles.line} />
            <span className={styles.rSlot}>
              <span className={`${styles.line} ${styles.rL2}`} style={{ width: '76%' }} />
              <span className={styles.rFinding}>尋找中…</span>
              <span className={styles.rFound}>第 2 頁</span>
            </span>
          </span>
        </span>
        <span className={`${styles.rTk} ${styles.rTk3}`}>
          <span className={styles.rNo}>3</span>
          <span className={styles.rBody}><span className={styles.line} /><span className={styles.line} style={{ width: '58%' }} /></span>
        </span>
        <span className={`${styles.rH} ${styles.rH2}`}>相似研報</span>
        <span className={`${styles.line} ${styles.rSim}`} />
      </div>

      <div className={styles.rCanvas}>
        <div className={styles.rThumbs}>
          {[1, 2, 3].map(n => (
            <span key={n} className={styles.rThumb} style={{ top: 12 + (n - 1) * 54 }}>
              <span className={styles.rThumbImg}>
                {['100%', '70%', '100%', '60%'].map((w, i) => <span key={i} className={styles.rTl} style={{ width: w }} />)}
                {n === 1 && <span className={styles.rBadge}>1</span>}
                {n === 2 && <span className={styles.rBadge}>2 3</span>}
              </span>
              <span className={styles.rThumbNo}>{n}</span>
            </span>
          ))}
          <span className={styles.rFrame} />
        </div>
        <div className={styles.rView}>
          <div className={styles.rScroll}>
            <div className={styles.rPage} style={{ top: 12 }}>
              <PageLines />
            </div>
            <div className={styles.rPage} style={{ top: 214 }}>
              <span className={styles.rHit} />
              <span className={styles.rSel} />
              <PageLines />
              <span className={styles.rDot}>2</span>
            </div>
          </div>
        </div>
        <div className={styles.rToastBox}>
          <span className={styles.rToast}><Check size={9} strokeWidth={3} />已複製</span>
        </div>
        <div className={styles.rDockBox}>
          <span className={styles.rDock}>
            <span className={styles.rIb}><PanelLeft size={11} strokeWidth={1.8} /></span>
            <span className={styles.rSep} />
            <span className={styles.rIb}><ChevronLeft size={11} strokeWidth={2} /></span>
            <span className={styles.rPg}>
              <span className={styles.rPg1}>1 / 6</span>
              <span className={styles.rPg2}>2 / 6</span>
            </span>
            <span className={styles.rIb}><ChevronRight size={11} strokeWidth={2} /></span>
            <span className={styles.rSep} />
            <span className={styles.rIb}><Minus size={11} strokeWidth={2} /></span>
            <span className={styles.rPct}>100%</span>
            <span className={styles.rIb}><Plus size={11} strokeWidth={2} /></span>
            <span className={styles.rSep} />
            <span className={styles.rIb}><Search size={11} strokeWidth={2} /></span>
            <span className={styles.rCopy}>
              <span className={styles.rSep} />
              <span className={`${styles.rIb} ${styles.rCopyBtn}`}><Copy size={11} strokeWidth={1.8} /></span>
            </span>
          </span>
        </div>
      </div>
      <Cursor className={styles.rCur} />
    </Demo>
  )
}

const PAGE_LINES: [number, number][] = [
  [34, 196], [48, 188], [62, 196], [76, 170], [90, 196], [104, 182], [118, 196], [132, 150], [146, 190], [160, 120],
]

function PageLines() {
  return (
    <>
      <span className={`${styles.line} ${styles.lineDark} ${styles.pLine}`} style={{ top: 16, width: 110 }} />
      {PAGE_LINES.map(([top, w]) => <span key={top} className={`${styles.line} ${styles.pLine}`} style={{ top, width: w }} />)}
    </>
  )
}

/** 研究助理：送出提問 → 思考 → 回答逐行出現、引用編號彈出 → 來源卡片 → 滑過引用編號浮出來源預覽。 */
export function AssistantDemo() {
  return (
    <Demo label="操作示範：送出提問，思考完成後回答逐步出現，下方列出來源，滑過引用編號預覽來源">
      <div className={styles.aBubble}>台積電明年資本支出怎麼看？</div>
      <div className={styles.aThink}>
        <span className={styles.aArcBox}>
          <svg className={styles.aArc} viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6" stroke="currentColor" strokeOpacity="0.2" strokeWidth="2" />
            <path d="M8 2a6 6 0 0 1 6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </span>
        <span className={styles.aLabels}>
          <span className={styles.aL1}>檢索研報…</span>
          <span className={styles.aL2}>已思考 3 秒{'\u3000'}參考 5 篇研報<ChevronDown size={11} strokeWidth={2} /></span>
        </span>
      </div>
      <div className={styles.aAns}>
        <span className={`${styles.aLine} ${styles.aA1}`} />
        <span className={`${styles.aLine} ${styles.aA2}`} />
        <span className={styles.aRow3}>
          <span className={`${styles.aLine} ${styles.aA3}`} />
          <span className={styles.aCite}>1</span>
        </span>
      </div>
      <div className={styles.aSrc}>
        {['台積電：先進封裝擴產提前', '晶圓代工：明年資本支出展望', '半導體設備：海外廠進度'].map(t => (
          <span key={t} className={`${styles.card} ${styles.aSrcCard}`}>
            <span className={styles.aSrcT}>{t}</span>
            <span className={`${styles.line} ${styles.aSrcM}`} />
          </span>
        ))}
        <span className={styles.aAll}>全部 5 則來源<ArrowRight size={10} strokeWidth={2} /></span>
      </div>
      <div className={styles.aActs}>
        <ThumbsUp size={11} strokeWidth={1.8} />
        <ThumbsDown size={11} strokeWidth={1.8} />
        <Copy size={11} strokeWidth={1.8} />
        <RotateCcw size={11} strokeWidth={1.8} />
      </div>
      <div className={styles.aPeek}>
        <span className={styles.aPeekT}>台積電：先進封裝擴產提前</span>
        <span className={styles.aPeekM}>凱基投顧{'\u3000'}10-27</span>
        <span className={`${styles.line} ${styles.markLine}`} style={{ width: '92%' }} />
      </div>
      <Cursor className={styles.aCur} />
    </Demo>
  )
}

const CAT_ROWS: { name: string; code: string; rating: 'buy' | 'neutral'; label: string; dist: [string, number][]; change: string; dir?: 'up' | 'down' }[] = [
  { name: '台積電', code: '2330', rating: 'buy', label: '買進', dist: [['dBuy', 6], ['dOw', 2], ['dNeu', 1]], change: '調升 1', dir: 'up' },
  { name: '鴻海', code: '2317', rating: 'buy', label: '買進', dist: [['dBuy', 4], ['dOw', 2], ['dNeu', 1]], change: '無變動' },
  { name: '聯電', code: '2303', rating: 'neutral', label: '中立', dist: [['dOw', 1], ['dNeu', 3], ['dUw', 1]], change: '調降 1', dir: 'down' },
]

const DOTS: [number, number, number][] = [[22, 55, 0], [41, 55, 0.15], [50, 41, 0.3], [58, 55, 0.45], [73, 55, 0.6]]

const THESIS: [string, string, string][] = [
  ['展望', '轉強', 'tUp'], ['催化劑', '轉強', 'tUp'], ['風險', '無顯著變化', 'tFlat'], ['估值', '分歧擴大', 'tDiv'],
]

/** 券商共識：在清單點一檔標的 → 詳情的三張摘要卡、各券商目標價點圖與四向觀點依序出現。 */
export function ConsensusDemo() {
  return (
    <Demo label="操作示範：在券商共識清單點選標的，進入詳情看共識評等、目標價中位數與各券商目標價分布">
      <div className={`${styles.card} ${styles.cCat}`}>
        <div className={styles.cHead}>
          <span className={styles.grow}>標的</span><span className={styles.cColR}>共識評等</span>
          <span className={styles.cColD}>評等分布</span><span className={styles.cColC}>近期變化</span>
        </div>
        {CAT_ROWS.map((r, i) => (
          <div key={r.code} className={i === 0 ? `${styles.cRow} ${styles.cRow1}` : styles.cRow}>
            <span className={`${styles.grow} ${styles.cName}`}>{r.name}<span className={styles.code}>{r.code}</span></span>
            <span className={styles.cColR}><span className={`${styles.badge} ${styles[r.rating]}`}>{r.label}</span></span>
            <span className={styles.cColD}><Dist parts={r.dist} /></span>
            <span className={`${styles.cColC} ${r.dir ? styles[r.dir] : styles.faint}`}>{r.change}</span>
          </div>
        ))}
      </div>
      <div className={styles.cDet}>
        <div className={styles.cDetHead}>
          <span className={styles.cDetName}>台積電</span>
          <span className={styles.code}>2330</span>
          <Mkt code="TW" label="台股" />
          <span className={styles.grow} />
          <span className={styles.cSeg}>
            {['30 天', '90 天', '180 天', '全部'].map(p => (
              <span key={p} className={p === '90 天' ? `${styles.cSegItem} ${styles.cSegOn}` : styles.cSegItem}>{p}</span>
            ))}
          </span>
        </div>
        <div className={styles.cCards}>
          <span className={`${styles.card} ${styles.cCard}`}>
            <span className={styles.cLbl}>共識評等</span>
            <span className={styles.cVal}><span className={`${styles.badge} ${styles.buy}`}>買進</span><Dist parts={[['dBuy', 6], ['dOw', 2], ['dNeu', 1]]} /></span>
          </span>
          <span className={`${styles.card} ${styles.cCard}`} style={{ animationDelay: '0.25s' }}>
            <span className={styles.cLbl}>目標價中位數</span>
            <span className={styles.cVal}><span className={styles.cNum}>1,400</span><span className={styles.cUnit}>元</span></span>
          </span>
          <span className={`${styles.card} ${styles.cCard}`} style={{ animationDelay: '0.5s' }}>
            <span className={styles.cLbl}>EPS 中位數</span>
            <span className={styles.cVal}><span className={styles.cNum}>63.6</span><span className={styles.cUnit}>元</span></span>
          </span>
        </div>
        <div className={`${styles.card} ${styles.cPlot}`}>
          <span className={styles.cLbl}>各券商目標價</span>
          <span className={styles.cAxis} />
          <span className={styles.cMed} />
          <span className={styles.cMedLbl}>中位數</span>
          {DOTS.map(([x, y, d]) => (
            <span key={x} className={styles.cDot} style={{ left: `${x}%`, top: y, animationDelay: `${d}s` }} />
          ))}
        </div>
        <div className={styles.cThesis}>
          {THESIS.map(([dim, label, cls], i) => (
            <span key={dim} className={`${styles.card} ${styles.cTh}`} style={{ animationDelay: `${i * 0.12}s` }}>
              <span className={styles.cThDim}>{dim}</span>
              <span className={`${styles.cThBadge} ${styles[cls]}`}>{label}</span>
            </span>
          ))}
        </div>
      </div>
      <Cursor className={styles.cCur} />
    </Demo>
  )
}

interface BriefDay {
  date: string
  weekday: string
  stats: [number, number, number]
  mix: [string, number][]
  sources: [string, string][]
}

const BRIEF_A: BriefDay = {
  date: '10 月 28 日', weekday: '2026 年 星期三', stats: [39, 4, 6],
  mix: [['TW', 19], ['US', 10], ['MACRO', 6], ['HK', 4]],
  sources: [['TW', '78%'], ['US', '64%']],
}
const BRIEF_B: BriefDay = {
  date: '10 月 27 日', weekday: '2026 年 星期二', stats: [31, 5, 7],
  mix: [['TW', 12], ['US', 9], ['MACRO', 5], ['CN', 3], ['GLOBAL', 2]],
  sources: [['US', '70%'], ['TW', '58%']],
}

function BriefColumn({ day, className }: { day: BriefDay; className: string }) {
  return (
    <div className={`${styles.bCol} ${className}`}>
      <div className={`${styles.card} ${styles.bOv}`}>
        <span className={styles.bDateRow}>
          <span className={styles.bDate}>{day.date}</span>
          <span className={styles.bWd}>{day.weekday}</span>
          <span className={styles.grow} />
          <span className={styles.bArr}><ChevronLeft size={10} strokeWidth={2} /></span>
          <span className={styles.bArr}><ChevronRight size={10} strokeWidth={2} /></span>
        </span>
        <span className={styles.bStats}>
          {(['篇新進研報', '個市場', '家券商'] as const).map((l, i) => (
            <span key={l} className={styles.bStat}><span className={styles.bStatN}>{day.stats[i]}</span>{l}</span>
          ))}
        </span>
        <span className={styles.bMix}>
          {day.mix.map(([code, n]) => <span key={code} style={{ flexGrow: n, background: marketColor(code) }} />)}
        </span>
      </div>
      <div className={`${styles.card} ${styles.bHl}`}>
        <span className={styles.bHlTitle}>今日重點</span>
        <span className={styles.bHlRow}><span className={`${styles.bNo} ${styles.bNoBig}`}>01</span><span className={`${styles.aLine} ${styles.grow}`} style={{ maxWidth: '82%', height: 8 }} /></span>
        <span className={styles.bHlRow}><span className={styles.bNo}>02</span><span className={`${styles.line} ${styles.grow}`} style={{ maxWidth: '66%' }} /></span>
        <span className={styles.bHlRow}><span className={styles.bNo}>03</span><span className={`${styles.line} ${styles.grow}`} style={{ maxWidth: '72%' }} /></span>
      </div>
    </div>
  )
}

function BriefSources({ day, className }: { day: BriefDay; className: string }) {
  return (
    <span className={`${styles.bSrc} ${className}`}>
      {day.sources.map(([code, w]) => (
        <span key={code} className={styles.bSrcRow}>
          <span className={styles.bSrcDot} style={{ background: marketColor(code) }} />
          <span className={styles.bSrcLines}>
            <span className={`${styles.line} ${styles.lineDim}`} style={{ width: w }} />
            <span className={styles.line} style={{ width: '40%' }} />
          </span>
        </span>
      ))}
    </span>
  )
}

const CAL_WEEKS = [[11, 12, 13, 14, 15, 16, 17], [18, 19, 20, 21, 22, 23, 24], [25, 26, 27, 28, 29, 30, 31]]
/** 有簡報的日期（月曆下方金色圓點）：平日，28 日之後還沒有。 */
const hasBrief = (d: number) => d <= 28 && ![17, 18, 24, 25].includes(d) && d !== 11

/** 每日簡報：在右側月曆點 27 日，左欄整欄重播進場、換成 27 日的內容。 */
export function BriefDemo() {
  return (
    <Demo label="操作示範：在右側月曆點選 27 日，左側簡報切換成 27 日的內容">
      <BriefColumn day={BRIEF_A} className={styles.bA} />
      <BriefColumn day={BRIEF_B} className={styles.bB} />
      <div className={`${styles.card} ${styles.bCal}`}>
        <span className={styles.bCalHead}>
          <span className={styles.bMonth}>2026 年 10 月</span>
          <span className={styles.grow} />
          <ChevronLeft size={10} strokeWidth={2} className={styles.faint} />
          <ChevronRight size={10} strokeWidth={2} className={styles.faint} />
        </span>
        <span className={styles.bWeek}>{['日', '一', '二', '三', '四', '五', '六'].map(w => <span key={w}>{w}</span>)}</span>
        <span className={styles.bGrid}>
          <span className={styles.bSelMark}><span /></span>
          {CAL_WEEKS.flat().map(d => {
            const cls = [styles.bDay]
            if (hasBrief(d)) cls.push(styles.bAvail)
            if (d === 27) cls.push(styles.b27)
            if (d === 28) cls.push(styles.b28, styles.bToday)
            return <span key={d} className={cls.join(' ')}>{d}</span>
          })}
        </span>
        <span className={styles.bSrcTitle}>本期來源研報</span>
        <BriefSources day={BRIEF_A} className={styles.bA} />
        <BriefSources day={BRIEF_B} className={styles.bB} />
      </div>
      <Cursor className={styles.bCur} />
    </Demo>
  )
}
