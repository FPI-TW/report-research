import { useRef, useState } from 'react'
import { marketLabel, marketTint, ptypeColor, MARKET_ORDER } from '../../lib/meta'
import { Icon } from '../../components/primitives/Icon'
import browseImg from '../../assets/help/browse.png'
import resultsImg from '../../assets/help/results.png'
import reportImg from '../../assets/help/report.png'
import askImg from '../../assets/help/ask.png'
import monitorImg from '../../assets/help/monitor.png'
import styles from './HelpPage.module.css'

const ACCESS_URL = 'http://192.168.1.128:8097/'

const MARKET_DESC: Record<string, string> = {
  TW: '台灣上市櫃個股／產業',
  US: '美國個股／市場（多為外資英文報告）',
  HK: '香港掛牌個股／市場',
  CN: '中國 A 股、陸股策略',
  WTX: '台指期、選擇權、部位策略',
  FX: '匯率、外匯',
  MACRO: '總經、利率、跨市場策略；債券／固收也歸這',
  GLOBAL: '全球資產配置；原物料／商品／能源也歸這',
  CRYPTO: '加密貨幣',
}

const PTYPES: [string, string][] = [
  ['股票', '個股'],
  ['指數', '指數／大盤'],
  ['期貨', '期貨'],
  ['選擇權', '選擇權'],
  ['ETF', 'ETF'],
  ['債券', '債券／固定收益'],
  ['外匯', '外匯'],
  ['原物料', '原物料、商品、能源、金屬'],
  ['加密', '加密貨幣'],
]

const TOC: [string, string][] = [
  ['start', '開始使用（網址）'],
  ['search', '檢索研報：瀏覽與搜尋'],
  ['filter', '篩選、排序與檢視'],
  ['result', '看懂一筆結果'],
  ['full', '查看完整報告（PDF）'],
  ['ask', '智能問答（向 AI 提問）'],
  ['radar', '廷豐觀點'],
  ['monitor', '導入監控（進階）'],
]

/** HTTP + 區網 IP 屬非安全情境，navigator.clipboard 會被擋 → execCommand 後備。 */
function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text)
  return new Promise((resolve, reject) => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.top = '-9999px'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    let ok: boolean
    try {
      ok = document.execCommand('copy')
    } catch {
      ok = false
    }
    document.body.removeChild(ta)
    if (ok) resolve()
    else reject(new Error('copy failed'))
  })
}

function CopyUrlButton({ url }: { url: string }) {
  const [label, setLabel] = useState('複製')
  const [done, setDone] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  function onClick() {
    copyText(url)
      .then(() => {
        setDone(true)
        setLabel('已複製')
      })
      .catch(() => setLabel('請手動複製'))
      .finally(() => {
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(() => {
          setDone(false)
          setLabel('複製')
        }, 1600)
      })
  }

  return (
    <button
      type="button"
      className={`${styles.copyBtn} ${done ? styles.copyDone : ''}`}
      onClick={onClick}
    >
      <Icon name={done ? 'check' : 'copy'} size={14} />
      {label}
    </button>
  )
}

function Figure({ src, alt, caption }: { src: string; alt: string; caption: string }) {
  return (
    <figure className={styles.figure}>
      <img src={src} alt={alt} loading="lazy" />
      <figcaption className={styles.figcaption}>{caption}</figcaption>
    </figure>
  )
}

export default function HelpPage() {
  return (
    <div className={`${styles.scroll} tf-scroll`}>
      <div className={styles.wrap}>
        <header className={styles.head}>
          <h1 className={styles.title}>
            廷豐智能<span className={styles.accent}>研報</span> 使用說明
          </h1>
          <p className={styles.lede}>
            廷豐智能研報把券商研報集中起來，用「語意 ＋ 關鍵字」混合搜尋，幾秒內找到相關報告與段落，
            並可直接閱讀原始 PDF、向 AI 提問、或用廷豐觀點追蹤各券商評等變化。本說明帶你快速上手。
          </p>
        </header>

        <nav className={styles.toc} aria-label="目錄">
          <div className={styles.tocLabel}>目錄</div>
          <ol className={styles.tocList}>
            {TOC.map(([id, label]) => (
              <li key={id}><a href={`#${id}`}>{label}</a></li>
            ))}
          </ol>
        </nav>

        <section id="start" className={styles.section}>
          <h2 className={styles.h2}><span className={styles.no}>1</span>開始使用</h2>
          <p>在<strong>公司網路</strong>內，用瀏覽器（建議 Chrome 或 Edge，手機也可以）打開：</p>
          <p className={styles.urlRow}>
            <span className={styles.url}>{ACCESS_URL}</span>
            <CopyUrlButton url={ACCESS_URL} />
          </p>
          <p>首次進入需以共用帳號登入，之後整個操作都在瀏覽器完成，不需要安裝任何東西。</p>
        </section>

        <section id="search" className={styles.section}>
          <h2 className={styles.h2}><span className={styles.no}>2</span>檢索研報：瀏覽與搜尋</h2>
          <p>
            進入後預設是<strong>檢索研報</strong>。還沒輸入查詢時，畫面下方會以<strong>最新入庫</strong>
            （日期新到舊）列出目前所有已導入的報告，可直接瀏覽。
          </p>
          <Figure
            src={browseImg}
            alt="檢索起始畫面"
            caption="起始畫面：品牌起始＋「檢索／問答」切換＋搜尋框；下方為「最新入庫」，左側為導覽與歷史對話"
          />
          <p>在<strong>搜尋框</strong>輸入主題後按 <strong>Enter</strong> 即可搜尋：</p>
          <ul className={styles.bul}>
            <li>可輸入主題、公司、事件，例如：「AI 伺服器散熱」、「台積電 先進封裝」、「美國 升息」。</li>
            <li>採<strong>語意 ＋ 關鍵字混合檢索</strong>：用字不必和報告完全相同，也能找到語意相關的報告。</li>
            <li>
              結果依<strong>相關度</strong>排序，每筆右側的<strong>百分比</strong>是「該篇
              <strong>最相關片段</strong>與查詢的相似度」；列表會列出<strong>命中片段</strong>，關鍵詞以
              <mark>底色</mark>標示。
            </li>
          </ul>
          <div className={styles.note}>
            搜尋回傳的是<strong>最相關的前幾篇</strong>研報（非整個資料庫）；想更精準就多加關鍵字縮小範圍。
            百分比是「最相關<strong>片段</strong>的相似度」，不是整篇報告的相關度。
          </div>
          <Figure
            src={resultsImg}
            alt="搜尋結果畫面"
            caption="搜尋「AI 伺服器散熱」的結果：依月份分組，每筆列出命中片段（關鍵詞黃底高亮），右側為相似度 %"
          />
        </section>

        <section id="filter" className={styles.section}>
          <h2 className={styles.h2}><span className={styles.no}>3</span>篩選、排序與檢視</h2>
          <p>工具列的篩選可單獨使用，也可與搜尋並用。每個項目後的數字是該分類的報告數：</p>
          <ul className={styles.bul}>
            <li><strong>市場</strong>：台股、美股、總經、全球… 點一下只看該市場（完整 9 種見下一節）。</li>
            <li><strong>商品類型</strong>：股票、指數、債券、原物料、ETF… 報告實際提到的金融商品。</li>
            <li><strong>標的</strong>：可切換「對選股有參考」或「對期貨／指數部位有參考」的報告。</li>
            <li><strong>報告類型</strong>：速報、週報、策略… 報告的類別（依檔名判定）。</li>
          </ul>
          <div className={styles.note}>想取消篩選，再點一次同一個項目，或點「全部」即可回到不限。</div>

          <h3 className={styles.h3}>排序</h3>
          <ul className={styles.bul}>
            <li><strong>搜尋時</strong>：<span className={styles.tagInline}>相關度</span>（預設）、日期新→舊、日期舊→新。</li>
            <li><strong>瀏覽時</strong>：<span className={styles.tagInline}>日期新→舊</span>（預設）、日期舊→新。</li>
          </ul>

          <h3 className={styles.h3}>檢視方式</h3>
          <p>工具列的<strong>檢視切換</strong>可改變報告呈現方式（瀏覽與搜尋皆適用），切換時<strong>不會重新查詢</strong>：</p>
          <ul className={styles.bul}>
            <li><span className={styles.tagInline}>列表</span>：一列一篇、密度高，可依<strong>日期(月)</strong>或<strong>市場</strong>分組。</li>
            <li><span className={styles.tagInline}>表格</span>：以欄位排列（名稱／市場／類型／日期／來源／標的），點欄位標題可排序。</li>
          </ul>
          <div className={styles.note}>
            分組與表格的欄位排序只作用於<strong>目前已載入</strong>的報告；瀏覽很多篇時，先點「載入更多」再排序。
            系統會<strong>記住</strong>你慣用的檢視方式。
          </div>
        </section>

        <section id="result" className={styles.section}>
          <h2 className={styles.h2}><span className={styles.no}>4</span>看懂一筆結果</h2>
          <p>一筆搜尋結果（列表檢視）由這些部位組成：</p>
          <ol className={styles.anatomy}>
            <li><span className={styles.n}>1</span><span><strong>市場徽章</strong>：報告涵蓋的主要市場（每篇一個，見下方 9 種）。</span></li>
            <li><span className={styles.n}>2</span><span><strong>報告名／檔名</strong>：原始研報標題。</span></li>
            <li><span className={styles.n}>3</span><span><strong>來源／日期</strong>：券商來源與報告日期。</span></li>
            <li><span className={styles.n}>4</span><span><strong>商品類型標籤</strong>：報告提到的金融商品（可 0～多個）。</span></li>
            <li><span className={styles.n}>5</span><span><strong>標的標籤</strong>：對選股／期貨是否有參考，常附<strong>代碼</strong>（如「個股 · 2330」）。</span></li>
            <li><span className={styles.n}>6</span><span><strong>命中片段</strong>：搜尋時列出最相關段落，關鍵詞以底色高亮；瀏覽時改顯示 2-3 句中文摘要。</span></li>
            <li><span className={styles.n}>7</span><span><strong>相關度</strong>：最右側百分比＝該篇最相關片段與查詢的相似度。</span></li>
          </ol>

          <h3 className={styles.h3}>市場徽章（每篇一個）</h3>
          <div className={styles.legend}>
            {MARKET_ORDER.map((code) => {
              const tint = marketTint(code)
              return (
                <div key={code} className={styles.lrow}>
                  <span className={styles.chip} style={tint}>
                    <span className={styles.dot} style={{ background: tint.color }} />
                    {marketLabel(code)}
                  </span>
                  <span className={styles.m}>{MARKET_DESC[code]}</span>
                </div>
              )
            })}
          </div>

          <h3 className={styles.h3}>商品類型標籤（可 0～多個）</h3>
          <div className={styles.legend}>
            {PTYPES.map(([label, desc]) => {
              const c = ptypeColor(label)
              return (
                <div key={label} className={styles.lrow}>
                  <span
                    className={styles.chip}
                    style={{ background: `color-mix(in srgb, ${c} 12%, white)`, color: c }}
                  >
                    {label}
                  </span>
                  <span className={styles.m}>{desc}</span>
                </div>
              )
            })}
          </div>

          <h3 className={styles.h3}>標的標籤</h3>
          <div className={styles.subjBlock}>
            <div className={styles.sbHead}>
              <span className={styles.subjChip}>個股</span>
              <span>對<strong>選股／個股交易</strong>有參考價值</span>
            </div>
            <div className={styles.sbEg}>常附個股代碼，例如「個股 · 2330」；多檔時顯示「個股 · 2330 等 5 檔」。</div>
          </div>
          <div className={styles.subjBlock}>
            <div className={styles.sbHead}>
              <span className={styles.subjChip}>期貨</span>
              <span>對<strong>期貨／指數部位</strong>有參考價值</span>
            </div>
            <div className={styles.sbEg}>常附期貨商品，例如「期貨 · 台指期」。詞表：台指期／小型台指／電子期／金融期／個股期貨。</div>
          </div>
          <div className={styles.note}>
            商品類型與標的若報告沒明確提及就不會出現；一筆結果可能同時有市場徽章 ＋ 多個商品類型 ＋ 標的標籤。
          </div>
        </section>

        <section id="full" className={styles.section}>
          <h2 className={styles.h2}><span className={styles.no}>5</span>查看完整報告（PDF）</h2>
          <p>點<strong>任一筆結果</strong>（列表或表格的整列；問答則點回答下方的來源），會彈出視窗<strong>內嵌原始 PDF</strong>，不必下載就能讀：</p>
          <ul className={styles.bul}>
            <li>可上下捲動閱讀；用 PDF 工具列可放大、下載、列印。</li>
            <li>點視窗外的灰色區域或右上<strong>關閉鈕</strong>，即可回到結果。</li>
          </ul>
          <Figure
            src={reportImg}
            alt="完整報告 PDF 內嵌檢視"
            caption="點任一筆結果，會在視窗內嵌原始券商 PDF，可直接閱讀，或在新分頁開啟／下載原始檔"
          />
        </section>

        <section id="ask" className={styles.section}>
          <h2 className={styles.h2}><span className={styles.no}>6</span>智能問答（向 AI 提問）</h2>
          <p>畫面上方有<strong>「檢索／問答」切換</strong>。切到<strong>問答</strong>後，可用自然語言提問，系統會檢索相關研報、由 AI 整理出帶<strong>引用來源</strong>的回答：</p>
          <ul className={styles.bul}>
            <li>在輸入框打字、按 <strong>Enter</strong> 送出，回答會逐字串流出現。</li>
            <li>回答中的引註（如 <strong>[1]</strong>）可點開對應來源；下方可展開完整<strong>資料來源</strong>清單。</li>
            <li>可對回答按<strong>讚／倒讚</strong>或<strong>複製</strong>，也可<strong>追問</strong>延續同一段對話。</li>
            <li>左側會列出<strong>歷史問答</strong>，點一下可重看；該筆右側的垃圾桶圖示可刪除（會再確認）。</li>
          </ul>
          <div className={styles.note}>
            問答以<strong>整個語料庫</strong>為範圍（不受檢索篩選影響）。若問題與研報內容無關，系統會婉拒並說明原因。
          </div>
          <Figure
            src={askImg}
            alt="智能問答畫面"
            caption="問答模式：上方思考步驟、主區為帶引註來源（鎏金 [n]）的回答與表格，下方為提問輸入框"
          />
        </section>

        <section id="radar" className={styles.section}>
          <h2 className={styles.h2}><span className={styles.no}>7</span>廷豐觀點</h2>
          <p><strong>廷豐觀點</strong>把同一標的的各券商研報彙整起來，追蹤一段期間內的<strong>觀點與評等變化</strong>：</p>
          <ul className={styles.bul}>
            <li>先選一個標的，即可看到<strong>評等共識</strong>、<strong>目標價／EPS 中位數</strong>與資料品質。</li>
            <li><strong>近期關鍵變化</strong>列出各券商的上修／下修事件，可點開原始研報佐證。</li>
            <li><strong>各券商最新觀點</strong>可展開單一券商的歷程；上方可切換 30／90／180 天等<strong>時間窗</strong>。</li>
          </ul>
        </section>

        <section id="monitor" className={styles.section}>
          <h2 className={styles.h2}><span className={styles.no}>8</span>導入監控（進階）</h2>
          <p><strong>導入監控</strong>主要給<strong>管理者</strong>查看資料導入狀況（一般使用者用不到）：</p>
          <ul className={styles.bul}>
            <li><strong>已導入報告數、總片段、標註進度、摘要進度</strong>等即時數字。</li>
            <li><strong>市場分佈</strong>長條圖，與四條背景管線（網頁／導入／標註／摘要）是否執行中。</li>
            <li>數字會定時自動刷新。</li>
          </ul>
          <div className={styles.note}>畫面上的「<strong>—</strong>」代表資料載入中或暫無數值，連上後會自動帶入。</div>
          <Figure
            src={monitorImg}
            alt="導入監控畫面"
            caption="導入監控：已導入報告／片段／標註／摘要進度、四條處理管線狀態，與市場分佈長條圖"
          />
        </section>

        <footer className={styles.foot}>廷豐金融科技 · 廷豐智能研報</footer>
      </div>
    </div>
  )
}
