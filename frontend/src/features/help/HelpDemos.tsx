import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import {
  ArrowRight, Check, ChevronDown, ChevronLeft, ChevronRight, Copy, Minus, PanelLeft, Plus, RotateCcw, Search,
  ThumbsDown, ThumbsUp,
} from 'lucide-react'
import { marketColor, marketTint } from '../../lib/meta'
import styles from './HelpDemos.module.css'
import { t } from '@/i18n'

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
    <Demo label={t('help.helpDemos.demoTypeAKeywordIn')}>
      <div className={`${styles.card} ${styles.lSearch}`}>
        <Search size={14} strokeWidth={2} className={styles.faint} />
        <span className={styles.lType}>{t('help.helpDemos.aiServerCooling')}</span>
        <span className={styles.grow} />
        <span className={styles.lEnter}>Enter</span>
      </div>
      <div className={styles.lChips}>
        <span className={`${styles.chip} ${styles.lAll}`}>{t('help.helpDemos.all')}<span className={styles.chipN}>24</span></span>
        <span className={`${styles.chip} ${styles.lTw}`}>{t('help.helpDemos.taiwan')}<span className={styles.chipN}>15</span></span>
        <span className={styles.chip}>{t('help.helpDemos.us')}<span className={styles.chipN}>9</span></span>
      </div>
      <div className={`${styles.card} ${styles.lRow} ${styles.lR1}`}>
        <span className={styles.lMkt} style={{ color: marketColor('TW') }}>{t('help.helpDemos.taiwan')}</span>
        <span className={styles.lTitle}>{t('help.helpDemos.serverCoolingLiquidCoolingAdoption')}</span>
        <span className={styles.top}>{t('help.helpDemos.mostRelevant')}</span>
        <span className={`${styles.line} ${styles.markLine}`} style={{ maxWidth: 80 }} />
        <span className={styles.lMeta}>{t('help.helpDemos.yuanta')}{t('help.helpDemos.text')}10-27</span>
      </div>
      <div className={`${styles.card} ${styles.lRow} ${styles.lR2}`}>
        <span className={styles.lMkt} style={{ color: marketColor('TW') }}>{t('help.helpDemos.taiwan')}</span>
        <span className={styles.lTitle}>{t('help.helpDemos.thermalModuleMakersAiRack')}</span>
        <span className={`${styles.line} ${styles.markLine}`} style={{ maxWidth: 60 }} />
        <span className={styles.lMeta}>{t('help.helpDemos.kgi')}{t('help.helpDemos.text')}10-24</span>
      </div>
      <div className={`${styles.card} ${styles.lRow} ${styles.lR3}`}>
        <span className={styles.lMkt} style={{ color: marketColor('US') }}>{t('help.helpDemos.us')}</span>
        <span className={styles.lTitle}>{t('help.helpDemos.dataCenterPowerAndCooling')}</span>
        <span className={`${styles.line} ${styles.markLine}`} style={{ maxWidth: 70 }} />
        <span className={styles.lMeta}>{t('help.helpDemos.jPMorgan')}{t('help.helpDemos.text')}10-22</span>
      </div>
      <Cursor className={styles.lCur} />
    </Demo>
  )
}

/** 閱讀頁：點第 2 條重點摘錄 → PDF 捲到第 2 頁並標出原文 → 在 PDF 上拖曳選字 → 工具列的複製鈕。 */
export function ReaderDemo() {
  return (
    <Demo label={t('help.helpDemos.demoClickAKeyTakeaway')}>
      <div className={`${styles.card} ${styles.rPanel}`}>
        <span className={`${styles.line} ${styles.lineDark} ${styles.rTitle}`} />
        <span className={`${styles.line} ${styles.rMeta1}`} />
        <span className={`${styles.line} ${styles.rMeta2}`} />
        <span className={`${styles.rH} ${styles.rH1}`}>{t('help.helpDemos.keyTakeaways')}</span>
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
              <span className={styles.rFinding}>{t('help.helpDemos.locating')}</span>
              <span className={styles.rFound}>{t('help.helpDemos.page2')}</span>
            </span>
          </span>
        </span>
        <span className={`${styles.rTk} ${styles.rTk3}`}>
          <span className={styles.rNo}>3</span>
          <span className={styles.rBody}><span className={styles.line} /><span className={styles.line} style={{ width: '58%' }} /></span>
        </span>
        <span className={`${styles.rH} ${styles.rH2}`}>{t('help.helpDemos.similarReports')}</span>
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
          <span className={styles.rToast}><Check size={9} strokeWidth={3} />{t('help.helpDemos.copied')}</span>
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
    <Demo label={t('help.helpDemos.demoSendAQuestionOnce')}>
      <div className={styles.aBubble}>{t('help.helpDemos.howDoBrokersViewTsmc')}</div>
      <div className={styles.aThink}>
        <span className={styles.aArcBox}>
          <svg className={styles.aArc} viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6" stroke="currentColor" strokeOpacity="0.2" strokeWidth="2" />
            <path d="M8 2a6 6 0 0 1 6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </span>
        <span className={styles.aLabels}>
          <span className={styles.aL1}>{t('help.helpDemos.searchingReports')}</span>
          <span className={styles.aL2}>{t('help.helpDemos.thoughtFor3S')}{t('help.helpDemos.text')}{t('help.helpDemos.referenced5Reports')}<ChevronDown size={11} strokeWidth={2} /></span>
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
        {[t('help.helpDemos.tsmcAdvancedPackagingExpansionBrought'), t('help.helpDemos.foundryCapitalExpenditureOutlookFor'), t('help.helpDemos.semiconductorEquipmentOverseasFabProgress')].map(item => (
          <span key={item} className={`${styles.card} ${styles.aSrcCard}`}>
            <span className={styles.aSrcT}>{item}</span>
            <span className={`${styles.line} ${styles.aSrcM}`} />
          </span>
        ))}
        <span className={styles.aAll}>{t('help.helpDemos.all5Sources')}<ArrowRight size={10} strokeWidth={2} /></span>
      </div>
      <div className={styles.aActs}>
        <ThumbsUp size={11} strokeWidth={1.8} />
        <ThumbsDown size={11} strokeWidth={1.8} />
        <Copy size={11} strokeWidth={1.8} />
        <RotateCcw size={11} strokeWidth={1.8} />
      </div>
      <div className={styles.aPeek}>
        <span className={styles.aPeekT}>{t('help.helpDemos.tsmcAdvancedPackagingExpansionBrought')}</span>
        <span className={styles.aPeekM}>{t('help.helpDemos.kgi')}{t('help.helpDemos.text')}10-27</span>
        <span className={`${styles.line} ${styles.markLine}`} style={{ width: '92%' }} />
      </div>
      <Cursor className={styles.aCur} />
    </Demo>
  )
}

const CAT_ROWS: { name: string; code: string; rating: 'buy' | 'neutral'; label: string; dist: [string, number][]; change: string; dir?: 'up' | 'down' }[] = [
  { get name() { return t('help.helpDemos.tsmc') }, code: '2330', rating: 'buy', get label() { return t('help.helpDemos.buy') }, dist: [['dBuy', 6], ['dOw', 2], ['dNeu', 1]], get change() { return t('help.helpDemos.1Upgrade') }, dir: 'up' },
  { get name() { return t('help.helpDemos.honHai') }, code: '2317', rating: 'buy', get label() { return t('help.helpDemos.buy') }, dist: [['dBuy', 4], ['dOw', 2], ['dNeu', 1]], get change() { return t('help.helpDemos.noChange') } },
  { get name() { return t('help.helpDemos.umc') }, code: '2303', rating: 'neutral', get label() { return t('help.helpDemos.neutral') }, dist: [['dOw', 1], ['dNeu', 3], ['dUw', 1]], get change() { return t('help.helpDemos.1Downgrade') }, dir: 'down' },
]

const DOTS: [number, number, number][] = [[22, 55, 0], [41, 55, 0.15], [50, 41, 0.3], [58, 55, 0.45], [73, 55, 0.6]]

const THESIS: [string, string, string][] = [
  ['展望', '轉強', 'tUp'], ['催化劑', '轉強', 'tUp'], ['風險', '無顯著變化', 'tFlat'], ['估值', '分歧擴大', 'tDiv'],
]

/** 券商共識：在清單點一檔標的 → 詳情的三張摘要卡、各券商目標價點圖與四向觀點依序出現。 */
export function ConsensusDemo() {
  return (
    <Demo label={t('help.helpDemos.demoClickAnInstrumentIn')}>
      <div className={`${styles.card} ${styles.cCat}`}>
        <div className={styles.cHead}>
          <span className={styles.grow}>{t('help.helpDemos.instrument')}</span><span className={styles.cColR}>{t('help.helpDemos.consensusRating')}</span>
          <span className={styles.cColD}>{t('help.helpDemos.ratingDistribution')}</span><span className={styles.cColC}>{t('help.helpDemos.recentChanges')}</span>
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
          <span className={styles.cDetName}>{t('help.helpDemos.tsmc')}</span>
          <span className={styles.code}>2330</span>
          <Mkt code="TW" label={t('help.helpDemos.taiwan')} />
          <span className={styles.grow} />
          <span className={styles.cSeg}>
            {[t('help.helpDemos.30Days'), t('help.helpDemos.90Days'), t('help.helpDemos.180Days'), t('help.helpDemos.all')].map(p => (
              <span key={p} className={p === '90 天' ? `${styles.cSegItem} ${styles.cSegOn}` : styles.cSegItem}>{p}</span>
            ))}
          </span>
        </div>
        <div className={styles.cCards}>
          <span className={`${styles.card} ${styles.cCard}`}>
            <span className={styles.cLbl}>{t('help.helpDemos.consensusRating')}</span>
            <span className={styles.cVal}><span className={`${styles.badge} ${styles.buy}`}>{t('help.helpDemos.buy')}</span><Dist parts={[['dBuy', 6], ['dOw', 2], ['dNeu', 1]]} /></span>
          </span>
          <span className={`${styles.card} ${styles.cCard}`} style={{ animationDelay: '0.25s' }}>
            <span className={styles.cLbl}>{t('help.helpDemos.medianTargetPrice')}</span>
            <span className={styles.cVal}><span className={styles.cNum}>1,400</span><span className={styles.cUnit}>{t('help.helpDemos.twd')}</span></span>
          </span>
          <span className={`${styles.card} ${styles.cCard}`} style={{ animationDelay: '0.5s' }}>
            <span className={styles.cLbl}>{t('help.helpDemos.medianEps')}</span>
            <span className={styles.cVal}><span className={styles.cNum}>63.6</span><span className={styles.cUnit}>{t('help.helpDemos.twd')}</span></span>
          </span>
        </div>
        <div className={`${styles.card} ${styles.cPlot}`}>
          <span className={styles.cLbl}>{t('help.helpDemos.targetPriceByBroker')}</span>
          <span className={styles.cAxis} />
          <span className={styles.cMed} />
          <span className={styles.cMedLbl}>{t('help.helpDemos.median')}</span>
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
  get date() { return t('help.helpDemos.oct28') }, get weekday() { return t('help.helpDemos.wed2026') }, stats: [39, 4, 6],
  mix: [['TW', 19], ['US', 10], ['MACRO', 6], ['HK', 4]],
  sources: [['TW', '78%'], ['US', '64%']],
}
const BRIEF_B: BriefDay = {
  get date() { return t('help.helpDemos.oct27') }, get weekday() { return t('help.helpDemos.tue2026') }, stats: [31, 5, 7],
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
          {([t('help.helpDemos.newReports'), t('help.helpDemos.markets'), t('help.helpDemos.brokers')] as const).map((l, i) => (
            <span key={l} className={styles.bStat}><span className={styles.bStatN}>{day.stats[i]}</span>{l}</span>
          ))}
        </span>
        <span className={styles.bMix}>
          {day.mix.map(([code, n]) => <span key={code} style={{ flexGrow: n, background: marketColor(code) }} />)}
        </span>
      </div>
      <div className={`${styles.card} ${styles.bHl}`}>
        <span className={styles.bHlTitle}>{t('help.helpDemos.todaySHighlights')}</span>
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
    <Demo label={t('help.helpDemos.demoClickThe27thIn')}>
      <BriefColumn day={BRIEF_A} className={styles.bA} />
      <BriefColumn day={BRIEF_B} className={styles.bB} />
      <div className={`${styles.card} ${styles.bCal}`}>
        <span className={styles.bCalHead}>
          <span className={styles.bMonth}>{t('help.helpDemos.october2026')}</span>
          <span className={styles.grow} />
          <ChevronLeft size={10} strokeWidth={2} className={styles.faint} />
          <ChevronRight size={10} strokeWidth={2} className={styles.faint} />
        </span>
        <span className={styles.bWeek}>{[t('help.helpDemos.s'), t('help.helpDemos.m'), t('help.helpDemos.t'), t('help.helpDemos.w'), t('help.helpDemos.t2'), t('help.helpDemos.f'), t('help.helpDemos.s2')].map(w => <span key={w}>{w}</span>)}</span>
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
        <span className={styles.bSrcTitle}>{t('help.helpDemos.sourceReports')}</span>
        <BriefSources day={BRIEF_A} className={styles.bA} />
        <BriefSources day={BRIEF_B} className={styles.bB} />
      </div>
      <Cursor className={styles.bCur} />
    </Demo>
  )
}
