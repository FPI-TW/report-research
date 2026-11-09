import { useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { ArrowRight, BarChart3, Check, Copy, FileText, Library, Lightbulb, MessageSquare, type LucideIcon } from 'lucide-react'
import { useFluidHover, useRegisterFluidHoverItem } from '@/hooks/use-fluid-hover'
import { FluidHoverHighlight } from '@/components/fluid-hover-highlight'
import { useStableRect } from '@/lib/useStableRect'
import { copyText } from '../../lib/clipboard'
import { useFeatures } from '../../lib/useFeatures'
import { useIsMac } from '../../lib/useIsMac'
import { useEnabledMarkets } from '../../lib/marketsConfig'
import { marketColor, marketLabel, type MARKET_ORDER } from '../../lib/meta'
import { preloadRoute, type RouteKey } from '../../lib/routePreload'
import { showToast } from '../../lib/toast'
import { useWebSearchPaused } from '../../lib/useWebSearch'
import { HELP_KEYS, HELP_TOC, MARKET_SCOPE, MOD, sectionDomId, type HelpSectionId } from './helpData'
import { AssistantDemo, BriefDemo, ConsensusDemo, LibraryDemo, ReaderDemo } from './HelpDemos'
import { useScrollSpy } from './useScrollSpy'
import styles from './HelpPage.module.css'
import { t } from '@/i18n'

/** 滑動高亮的過渡：與側欄、設定彈窗相同（交接包 0.3s 主曲線）。 */
const GLIDE = { duration: 0.3, ease: [0.32, 0.72, 0, 1] as const }

const TOC_IDS = HELP_TOC.map(item => item.id)

const CARDS: { to: string; title: string; desc: string; icon: LucideIcon; preload: RouteKey }[] = [
  { to: '/library', get title() { return t('help.helpPage.library') }, get desc() { return t('help.helpPage.searchAndBrowseBrokerResearch') }, icon: Library, preload: 'search' },
  { to: '/assistant', get title() { return t('help.helpPage.assistant') }, get desc() { return t('help.helpPage.askQuestionsAndGetAnswers') }, icon: MessageSquare, preload: 'ask' },
  { to: '/consensus', get title() { return t('help.helpPage.consensus') }, get desc() { return t('help.helpPage.consensusOfEachBrokerS') }, icon: BarChart3, preload: 'radar' },
  { to: '/brief', get title() { return t('help.helpPage.dailyBrief') }, get desc() { return t('help.helpPage.highlightsOfThePreviousDay') }, icon: FileText, preload: 'brief' },
]

type Register = (index: number, element: HTMLElement | null) => void

/** 一列只登錄一次（行內 ref callback 每次重繪都會解除再登錄，滑動高亮量不到位置）。 */
function TocRow({ register, index, label, on, onPick }: { register: Register; index: number; label: string; on: boolean; onPick: () => void }) {
  const ref = useRef<HTMLButtonElement>(null)
  useRegisterFluidHoverItem(register, index, ref)
  return (
    <button
      ref={ref}
      type="button"
      className={styles.tocRow}
      data-on={on || undefined}
      aria-current={on ? 'location' : undefined}
      onClick={onPick}
    >
      {label}
    </button>
  )
}

function Toc({ active, onPick }: { active: HelpSectionId; onPick: (id: HelpSectionId) => void }) {
  const listRef = useRef<HTMLDivElement>(null)
  const hover = useFluidHover(listRef)
  const activeRect = useStableRect(hover, TOC_IDS.indexOf(active))
  return (
    <nav className={styles.toc} aria-label={t('help.helpPage.helpContents')}>
      <div ref={listRef} className={styles.tocList} {...hover.handlers}>
        <FluidHoverHighlight rect={activeRect} session={0} fadeIn={false} transition={GLIDE} className={styles.hlActive} />
        <FluidHoverHighlight hover={hover} transition={GLIDE} className={styles.hlHover} />
        {HELP_TOC.map((item, i) => (
          <TocRow key={item.id} register={hover.registerItem} index={i} label={item.label} on={item.id === active} onPick={() => onPick(item.id)} />
        ))}
      </div>
    </nav>
  )
}

/** 平台網址＋複製。網址取目前這個站：正式與測試環境各自顯示自己的網址，不寫死主機。 */
function PlatformUrl() {
  const url = window.location.origin
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const inputRef = useRef<HTMLInputElement>(null)

  function onCopy() {
    copyText(url)
      .then(() => {
        setCopied(true)
        clearTimeout(timer.current)
        timer.current = setTimeout(() => setCopied(false), 1500)
      })
      .catch(() => {
        // 非安全情境又沒有 execCommand：選起來讓人自己按複製。
        inputRef.current?.select()
        showToast(t('help.helpPage.copyFailedCopyManually'))
      })
  }

  return (
    <div className={styles.urlField}>
      <label htmlFor="help-url" className={styles.srOnly}>{t('help.helpPage.platformUrl')}</label>
      <input ref={inputRef} id="help-url" className={styles.urlInput} type="text" readOnly value={url} onFocus={e => e.currentTarget.select()} />
      <button type="button" className={styles.copyBtn} onClick={onCopy}>
        {copied
          ? <><Check size={14} strokeWidth={2.2} className={styles.copiedIcon} aria-hidden />{t('help.helpPage.copied')}</>
          : <><Copy size={14} strokeWidth={1.8} aria-hidden />{t('help.helpPage.copy')}</>}
      </button>
    </div>
  )
}

function Section({ id, title, children }: { id: HelpSectionId; title: string; children: ReactNode }) {
  const dom = sectionDomId(id)
  return (
    <section id={dom} className={styles.sec} aria-labelledby={`${dom}-title`}>
      <h2 id={`${dom}-title`} className={styles.h2} tabIndex={-1}>{title}</h2>
      {children}
    </section>
  )
}

function Steps({ items }: { items: ReactNode[] }) {
  return (
    <ol className={styles.steps}>
      {items.map((it, i) => (
        <li key={i}><span className={styles.stepNo} aria-hidden>{i + 1}</span><span>{it}</span></li>
      ))}
    </ol>
  )
}

function Tip({ children }: { children: ReactNode }) {
  return (
    <div className={styles.tip}>
      <Lightbulb size={16} strokeWidth={1.8} aria-hidden className={styles.tipIcon} />
      <span>{children}</span>
    </div>
  )
}

function Kbd({ children }: { children: ReactNode }) {
  return <kbd className={styles.kbd}>{children}</kbd>
}

function Cite({ n }: { n: number }) {
  return <span className={styles.cite}>{n}</span>
}

/**
 * 使用說明（交接包「使用說明」）：左側目錄（scroll-spy、滑動高亮；<900px 隱藏）＋右側內容。
 * 頂部四張捷徑卡；研報庫、閱讀頁、研究助理、券商共識、每日簡報各有一段操作示範動畫（`HelpDemos`）。
 *
 * 文案描述的是**現在的**頁面：改了某一頁的動線，這裡的步驟、示範與快捷鍵表（`helpData.ts`）要一起改。
 */
type MarketCode = (typeof MARKET_ORDER)[number]

export default function HelpPage() {
  useFeatures()
  const enabledMarkets = useEnabledMarkets()
  const webPaused = useWebSearchPaused()
  const mac = useIsMac()
  const scrollRef = useRef<HTMLDivElement>(null)
  const { active, jump } = useScrollSpy(TOC_IDS, sectionDomId, scrollRef)
  const mod = mac ? '⌘' : 'Ctrl'

  return (
    <div className={styles.page}>
      <div ref={scrollRef} className={`${styles.scroll} tf-scroll`}>
        <div className={styles.wrap}>
          <Toc active={active} onPick={jump} />

          <article className={styles.article}>
            <h1 className={styles.title}>{t('help.helpPage.help')}</h1>
            <div className={styles.cards}>
              {CARDS.map(c => {
                const Icon = c.icon
                return (
                  <Link key={c.to} to={c.to} className={styles.card} onPointerEnter={() => preloadRoute(c.preload)}>
                    <span className={styles.cardIcon}><Icon size={17} strokeWidth={1.6} aria-hidden /></span>
                    <span className={styles.cardText}>
                      <span className={styles.cardTitle}>{c.title}</span>
                      <span className={styles.cardDesc}>{c.desc}</span>
                    </span>
                    <ArrowRight size={15} strokeWidth={1.8} aria-hidden className={styles.cardArrow} />
                  </Link>
                )
              })}
            </div>

            <Section id="start" title={t('help.helpPage.gettingStarted')}>
              <p className={styles.p}>
                {t('help.helpPage.openThePlatformUrlIn')}
              </p>
              <PlatformUrl />
            </Section>

            <Section id="library" title={t('help.helpPage.library')}>
              <p className={styles.p}>{t('help.helpPage.browseAndSearchAllIngested')}</p>
              <LibraryDemo />
              <Steps items={[
                <>{t('help.helpPage.typeATopicCompanyOr')}{' '}<Kbd>Enter</Kbd>{t('help.helpPage.forExampleAiServerCooling')}</>,
                <>{t('help.helpPage.narrowTheScopeWithThe')}</>,
                <>{t('help.helpPage.clickAnyReportToPreview')}</>,
              ]} />
              <Tip>{t('help.helpPage.searchMatchesBothMeaningAnd')}</Tip>
            </Section>

            <Section id="reader" title={t('help.helpPage.readingPage')}>
              <p className={styles.p}>{t('help.helpPage.theLeftSideShowsThe')}</p>
              <ReaderDemo />
              <ul className={styles.list}>
                <li>{t('help.helpPage.clickAKeyTakeawayAnd')}</li>
                <li>{t('help.helpPage.theGoldNumberAtThe')}</li>
                <li>{t('help.helpPage.dragToSelectTextDirectly')}{' '}<Kbd>{mod}</Kbd> <Kbd>C</Kbd>{t('help.helpPage.selectionsCanSpanPages')}</li>
                <li>{t('help.helpPage.theBottomToolbarTogglesThumbnails')}</li>
                <li>{t('help.helpPage.reportsWithRatingDataShow')}</li>
                <li>{t('help.helpPage.askAboutThisReportTakes')}</li>
              </ul>
              <Tip>{t('help.helpPage.aFewReportsAreWord')}</Tip>
            </Section>

            <Section id="assistant" title={t('help.helpPage.assistant')}>
              <p className={styles.p}>{t('help.helpPage.askInNaturalLanguageThe')}</p>
              <AssistantDemo />
              <Steps items={[
                <>{t('help.helpPage.typeAQuestionAndPress')}{' '}<Kbd>Enter</Kbd>{' '}{t('help.helpPage.toSendTheAnswerAppears')}</>,
                <>{t('help.helpPage.numbersInTheAnswerSuch')}{' '}<Cite n={1} />{' '}{t('help.helpPage.areCitedSourcesHoverTo')}</>,
                <>{t('help.helpPage.belowTheAnswerHelpfulNot')}</>,
              ]} />
              <ul className={styles.list}>
                <li>{t('help.helpPage.theAssistantCoversAllReports')}</li>
                <li>{t('help.helpPage.recentInTheSidebarKeeps')}</li>
                {!webPaused && <li>{t('help.helpPage.theInTheInputBox')}</li>}
              </ul>
              <Link to="/assistant" className={styles.more}>{t('help.helpPage.openAssistant')}<ArrowRight size={14} strokeWidth={1.8} aria-hidden /></Link>
            </Section>

            <Section id="consensus" title={t('help.helpPage.consensus')}>
              <p className={styles.p}>{t('help.helpPage.forEachInstrumentCompilesEach')}</p>
              <ConsensusDemo />
              <ul className={styles.list}>
                <li>{t('help.helpPage.searchTheListByInstrument')}</li>
                <li>{t('help.helpPage.theConsensusRatingIsThe')}</li>
                <li>{t('help.helpPage.switchingThePeriod30Days')}</li>
                <li>{t('help.helpPage.inTheTargetPriceChart')}</li>
                <li>{t('help.helpPage.theFourWayViewCovers')}</li>
                <li>{t('help.helpPage.clickABrokerSRow')}</li>
              </ul>
            </Section>

            <Section id="brief" title={t('help.helpPage.dailyBrief')}>
              <p className={styles.p}>{t('help.helpPage.everyMorningTheReportsIngested')}</p>
              <BriefDemo />
              <ul className={styles.list}>
                <li>{t('help.helpPage.datesWithAGoldDot')}</li>
                <li>{t('help.helpPage.sourceReportsAreListedBelow')}</li>
              </ul>
            </Section>

            <Section id="markets" title={t('help.helpPage.marketCodes')}>
              <p className={styles.p}>{t('help.helpPage.eachReportIsTaggedWith')}</p>
              <div className={styles.tile}>
                <table className={styles.table}>
                  <thead>
                    <tr><th scope="col">{t('help.helpPage.code')}</th><th scope="col">{t('help.helpPage.market')}</th><th scope="col">{t('help.helpPage.scope')}</th></tr>
                  </thead>
                  <tbody>
                    {(enabledMarkets as readonly MarketCode[]).map(code => (
                      <tr key={code}>
                        <td className={styles.mktCode} style={{ color: marketColor(code) }}>{code}</td>
                        <td>{marketLabel(code)}</td>
                        <td className={styles.scope}>{MARKET_SCOPE[code]}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>

            <Section id="keys" title={t('help.helpPage.keyboardShortcuts')}>
              <div className={styles.tile}>
                <table className={`${styles.table} ${styles.keys}`}>
                  <thead className={styles.srOnly}>
                    <tr><th scope="col">{t('help.helpPage.action')}</th><th scope="col">{t('help.helpPage.where')}</th><th scope="col">{t('help.helpPage.keys')}</th></tr>
                  </thead>
                  <tbody>
                    {HELP_KEYS.map(k => (
                      <tr key={`${k.where}-${k.what}`}>
                        <td>{k.what}</td>
                        <td className={styles.where}>{k.where}</td>
                        <td className={styles.keyCell}>
                          <span className={styles.keyList}>{k.keys.map(x => <Kbd key={x}>{x === MOD ? mod : x}</Kbd>)}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>

            {/* AI 生成揭露：要向終端使用者揭露並標示 AI 生成。
                只寫確定的事實：哪些功能由它生成、資料會送去哪裡；供應商怎麼處理以其條款為準，這裡不代為承諾。
                供應商寫成「目前為」：既有的摘要、標題、摘錄與標註多半由先前的模型產出，不能說全部出自現行模型。 */}
            <Section id="ai" title={t('help.helpPage.aiGeneratedContent')}>
              <p className={styles.p}>{t('help.helpPage.theFollowingContentOnThis')}</p>
              <ul className={styles.list}>
                <li>{t('help.helpPage.assistantAnswers')}</li>
                <li>{t('help.helpPage.reportSummariesTitlesAndThe')}</li>
                <li>{t('help.helpPage.reportTagsMarketAssetClass')}</li>
                <li>{t('help.helpPage.consensusRatingsTargetPricesEps')}</li>
                <li>{t('help.helpPage.dailyBriefs')}</li>
              </ul>
              <p className={styles.p}>{t('help.helpPage.duringGenerationQuestionsAnswersAnd')}</p>
              <p className={styles.p}>
                {t('help.helpPage.aiGeneratedContentMayContain')}
              </p>
              {!webPaused && <p className={styles.p}>{t('help.helpPage.withWebSearchOnAnswers')}</p>}
              <p className={styles.p}>{t('help.helpPage.questionHistoryIsKeptUnder')}</p>
            </Section>
          </article>
        </div>
      </div>
    </div>
  )
}
