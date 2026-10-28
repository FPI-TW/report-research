import { useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { ArrowRight, BarChart3, Check, Copy, FileText, Library, Lightbulb, MessageSquare, type LucideIcon } from 'lucide-react'
import { useFluidHover, useRegisterFluidHoverItem } from '@/hooks/use-fluid-hover'
import { FluidHoverHighlight } from '@/components/fluid-hover-highlight'
import { useStableRect } from '@/lib/useStableRect'
import { copyText } from '../../lib/clipboard'
import { useFeatures } from '../../lib/useFeatures'
import { useIsMac } from '../../lib/useIsMac'
import { MARKET_ORDER, marketColor, marketLabel } from '../../lib/meta'
import { preloadRoute, type RouteKey } from '../../lib/routePreload'
import { showToast } from '../../lib/toast'
import { useWebSearchPaused } from '../../lib/useWebSearch'
import { HELP_KEYS, HELP_TOC, MARKET_SCOPE, MOD, sectionDomId, type HelpSectionId } from './helpData'
import { AssistantDemo, BriefDemo, ConsensusDemo, LibraryDemo, ReaderDemo } from './HelpDemos'
import { useScrollSpy } from './useScrollSpy'
import styles from './HelpPage.module.css'

/** 滑動高亮的過渡：與側欄、設定彈窗相同（交接包 0.3s 主曲線）。 */
const GLIDE = { duration: 0.3, ease: [0.32, 0.72, 0, 1] as const }

const TOC_IDS = HELP_TOC.map(t => t.id)

const CARDS: { to: string; title: string; desc: string; icon: LucideIcon; preload: RouteKey }[] = [
  { to: '/library', title: '研報庫', desc: '搜尋與瀏覽券商研報', icon: Library, preload: 'search' },
  { to: '/assistant', title: '研究助理', desc: '提問並取得附來源的回答', icon: MessageSquare, preload: 'ask' },
  { to: '/consensus', title: '券商共識', desc: '各家評等與目標價的共識', icon: BarChart3, preload: 'radar' },
  { to: '/brief', title: '每日簡報', desc: '前一日研報的重點彙整', icon: FileText, preload: 'brief' },
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
    <nav className={styles.toc} aria-label="說明目錄">
      <div ref={listRef} className={styles.tocList} {...hover.handlers}>
        <FluidHoverHighlight rect={activeRect} session={0} fadeIn={false} transition={GLIDE} className={styles.hlActive} />
        <FluidHoverHighlight hover={hover} transition={GLIDE} className={styles.hlHover} />
        {HELP_TOC.map((t, i) => (
          <TocRow key={t.id} register={hover.registerItem} index={i} label={t.label} on={t.id === active} onPick={() => onPick(t.id)} />
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
        showToast('複製失敗，請手動複製')
      })
  }

  return (
    <div className={styles.urlField}>
      <label htmlFor="help-url" className={styles.srOnly}>平台網址</label>
      <input ref={inputRef} id="help-url" className={styles.urlInput} type="text" readOnly value={url} onFocus={e => e.currentTarget.select()} />
      <button type="button" className={styles.copyBtn} onClick={onCopy}>
        {copied
          ? <><Check size={14} strokeWidth={2.2} className={styles.copiedIcon} aria-hidden />已複製</>
          : <><Copy size={14} strokeWidth={1.8} aria-hidden />複製</>}
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
export default function HelpPage() {
  useFeatures()
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
            <h1 className={styles.title}>使用說明</h1>
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

            <Section id="start" title="開始使用">
              <p className={styles.p}>
                用瀏覽器開啟平台網址，以管理者配發的個人帳號登入。不需要安裝任何軟體，手機也能使用，建議使用 Chrome 或 Edge。
              </p>
              <PlatformUrl />
            </Section>

            <Section id="library" title="研報庫">
              <p className={styles.p}>瀏覽與搜尋所有已入庫的券商研報。還沒搜尋時，首頁列出頭條、最新評等變動、今日簡報與最新入庫的研報。</p>
              <LibraryDemo />
              <Steps items={[
                <>在搜尋框輸入主題、公司或事件後按 <Kbd>Enter</Kbd>，例如「AI 伺服器散熱」「台積電 先進封裝」。</>,
                <>用市場篩選縮小範圍，每個市場後面的數字是研報篇數；「更多篩選」可再依商品類型、報告類型與標的篩選。</>,
                <>點任一篇開啟預覽，再按「開啟閱讀頁」看原始 PDF，或按「就這篇提問」到研究助理。</>,
              ]} />
              <Tip>搜尋同時比對語意與關鍵字，用字不必和報告完全相同。</Tip>
            </Section>

            <Section id="reader" title="閱讀頁">
              <p className={styles.p}>左側是摘要、重點摘錄與相似研報，右側是原始券商 PDF。</p>
              <ReaderDemo />
              <ul className={styles.list}>
                <li>點重點摘錄，PDF 會捲到原文並標出位置，摘錄下方顯示在第幾頁；找不到時會顯示「原文中找不到這段文字」。</li>
                <li>縮圖右上的金色編號是摘錄所在的頁面，一頁有三條以上時顯示區間，例如「1–5」。</li>
                <li>可以直接在 PDF 上拖曳選字，再按底部工具列的複製鈕或 <Kbd>{mod}</Kbd> <Kbd>C</Kbd>，跨頁也可以。</li>
                <li>底部工具列可以開關縮圖、翻頁、縮放，以及在文件內搜尋。</li>
                <li>有評等資料的研報會顯示「本篇評等」，點擊即前往券商共識看各家看法。</li>
                <li>「就這篇提問」會帶著報告標題到研究助理；閱讀頁有獨立網址，可以直接分享給同事。</li>
              </ul>
              <Tip>少數研報的原始檔是 Word，無法內嵌顯示，閱讀頁改列出抽取的文字，原始檔可以下載。</Tip>
            </Section>

            <Section id="assistant" title="研究助理">
              <p className={styles.p}>用自然語言提問，系統會先檢索研報，再由 AI 整理成附引用來源的回答。</p>
              <AssistantDemo />
              <Steps items={[
                <>輸入問題後按 <Kbd>Enter</Kbd> 送出，回答會逐步出現，生成中可以按停止。</>,
                <>回答中的編號如 <Cite n={1} /> 是引用來源，滑過可預覽，點擊在右側展開全部來源。</>,
                <>回答下方可讚、倒讚、複製、重新生成，或點延伸提問繼續追問。</>,
              ]} />
              <ul className={styles.list}>
                <li>研究助理以全部研報為範圍，不受研報庫的篩選影響。</li>
                <li>側欄「最近」保存過去的對話，「搜尋」可以找回舊對話。</li>
                {!webPaused && <li>輸入框的「＋」可以開啟網路搜尋，回答會納入網路公開資訊。</li>}
              </ul>
              <Link to="/assistant" className={styles.more}>開啟研究助理<ArrowRight size={14} strokeWidth={1.8} aria-hidden /></Link>
            </Section>

            <Section id="consensus" title="券商共識">
              <p className={styles.p}>每檔標的彙整各家券商的評等、目標價與 EPS，以及觀點的變化。</p>
              <ConsensusDemo />
              <ul className={styles.list}>
                <li>清單可以用標的名稱或代號搜尋，依立場（偏多、中立、偏空）與市場篩選。</li>
                <li>共識評等取各家最新評等的中位數，目標價與 EPS 同樣取中位數。</li>
                <li>切換期間（30 天、90 天、180 天、全部）會重新計算共識與變化幅度。</li>
                <li>各券商目標價圖上一個點是一家券商，滑過可看券商與價位，虛線是中位數。</li>
                <li>四向觀點依展望、催化劑、風險、估值，標示各家看法轉強、轉弱、分歧擴大或無顯著變化。</li>
                <li>點券商那一列可以展開該券商歷次的觀點。</li>
              </ul>
            </Section>

            <Section id="brief" title="每日簡報">
              <p className={styles.p}>每天上午彙整前一日入庫的研報，整理成今日重點與各市場焦點。</p>
              <BriefDemo />
              <ul className={styles.list}>
                <li>右側月曆有金色圓點的日期都有簡報，點選即可切換；日期旁的箭頭可以切到前一份或後一份。</li>
                <li>月曆下方列出本期來源研報，點擊可開啟閱讀頁。</li>
              </ul>
            </Section>

            <Section id="markets" title="市場代碼">
              <p className={styles.p}>每篇研報會標上一個市場，篩選與共識都依此分類。</p>
              <div className={styles.tile}>
                <table className={styles.table}>
                  <thead>
                    <tr><th scope="col">代碼</th><th scope="col">市場</th><th scope="col">範圍</th></tr>
                  </thead>
                  <tbody>
                    {MARKET_ORDER.map(code => (
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

            <Section id="keys" title="快捷鍵">
              <div className={styles.tile}>
                <table className={`${styles.table} ${styles.keys}`}>
                  <thead className={styles.srOnly}>
                    <tr><th scope="col">動作</th><th scope="col">位置</th><th scope="col">按鍵</th></tr>
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

            {/* AI 生成揭露（DeepSeek 遷移 PR-U）：DeepSeek 條款要求向終端使用者揭露並標示 AI 生成。
                只寫確定的事實：哪些功能由它生成、資料會送去哪裡；供應商怎麼處理以其條款為準，這裡不代為承諾。
                供應商寫成「目前為」：既有的摘要、標題、摘錄與標註多半是遷移前由 Claude 產出，不能說全部出自 DeepSeek。 */}
            <Section id="ai" title="AI 生成內容">
              <p className={styles.p}>本站以下內容由大型語言模型（目前為 DeepSeek）自動生成，不是人工撰寫：</p>
              <ul className={styles.list}>
                <li>研究助理的回答。</li>
                <li>研報的摘要、標題與重點摘錄的論點。</li>
                <li>研報的標註（市場、商品類型、標的）。</li>
                <li>券商共識的評等、目標價、EPS 與觀點擷取。</li>
                <li>每日簡報。</li>
              </ul>
              <p className={styles.p}>生成時，提問、回答與相關的研報內容會送往模型供應商（目前為 DeepSeek）處理，並依其服務條款處理。</p>
              <p className={styles.p}>
                AI 生成的內容可能有誤（包括數字與引用），投資決策請以原始研報與公開資訊為準。重點摘錄的引文由 AI 從原文逐字摘出，可在閱讀頁點擊摘錄，對照原始 PDF 核對。
              </p>
              {!webPaused && <p className={styles.p}>開啟網路搜尋時，回答會納入網路公開資訊，這類資訊並非受信任的行情來源。</p>}
              <p className={styles.p}>提問紀錄保存在個人帳號下，可以在側欄「最近」刪除對話。</p>
            </Section>
          </article>
        </div>
      </div>
    </div>
  )
}
