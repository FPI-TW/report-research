import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import pdfiumWasmUrl from '@embedpdf/pdfium/pdfium.wasm?url'
import { createPluginRegistration } from '@embedpdf/core'
import { EmbedPDF } from '@embedpdf/core/react'
import { usePdfiumEngine } from '@embedpdf/engines/react'
import {
  DocumentContent,
  DocumentManagerPluginPackage,
} from '@embedpdf/plugin-document-manager/react'
import { RenderLayer, RenderPluginPackage } from '@embedpdf/plugin-render/react'
import { Rotate, RotatePluginPackage, useRotate } from '@embedpdf/plugin-rotate/react'
import { SearchLayer, SearchPluginPackage, useSearch } from '@embedpdf/plugin-search/react'
import { Scroller, ScrollPluginPackage, useScroll } from '@embedpdf/plugin-scroll/react'
import { ThumbImg, ThumbnailsPane, ThumbnailPluginPackage } from '@embedpdf/plugin-thumbnail/react'
import {
  Viewport,
  ViewportPluginPackage,
  useViewportScrollActivity,
} from '@embedpdf/plugin-viewport/react'
import { ZoomMode, ZoomPluginPackage, useZoom } from '@embedpdf/plugin-zoom/react'
import { Icon } from '../../../components/primitives/Icon'
import { CJK_FONT_FALLBACK } from './fontFallback'
import styles from './PdfViewer.module.css'

interface Props {
  /** 研報原始檔位址（`/api/report/{id}/file`，同源，帶 session cookie） */
  url: string
  /** 無障礙名稱，用研報的顯示標題 */
  title: string
}

/**
 * 引擎＋文件的就緒上限，逾時即拋 → `ViewerBoundary` 退回瀏覽器內建檢視。
 *
 * **存在理由是 2026-07-31 的生產事故**：引擎回傳了 handle、`EmbedPDF` 也掛載了，
 * 但文件永遠開不起來（`activeDocumentId` 恆為 falsy），畫面只剩一個空的 root div
 * ——不拋錯、不降級、console 零訊息。`ViewerBoundary` 只接得住**拋出來的例外**，
 * 接不住「永遠不完成」，所以那次降級機制等同不存在，讀者對著空白乾等到放棄。
 *
 * 15 秒是給冷啟動留足餘裕後的值：`worker: false` 之後 4.6MB 的 wasm 要在主執行緒
 * instantiate，正常情況遠低於此。**寧可偶爾誤判降級到內建檢視（讀者仍讀得到），
 * 也不要再有一次無聲的空白**。
 */
const READY_TIMEOUT_MS = 15_000

/** 縮圖寬度（px）。與 PdfViewer.module.css 的 `.thumbs` 欄寬 96px 綁在一起，見註冊處。 */
const THUMB_WIDTH = 72

/** 引擎啟動中：給紙張骨架而不是轉圈圈，載入完成時版面不跳動。 */
function Booting() {
  return (
    <div className={styles.booting} role="status">
      <div className={styles.bootPaper} />
      <span>正在啟動 PDF 引擎…</span>
    </div>
  )
}

interface ChromeProps {
  documentId: string
  url: string
  title: string
  thumbsOpen: boolean
  onToggleThumbs: () => void
  searchOpen: boolean
  onToggleSearch: () => void
  /** 由 ViewerBody 的鍵盤處理器遞增，用來把焦點送進搜尋框（每次遞增觸發一次） */
  focusSearchTick: number
}

/**
 * 工具列與縮圖列。
 *
 * 所有控制項都掛在 EmbedPDF provider 之內，因為 useZoom/useScroll 需要 plugin context。
 */
function Chrome({
  documentId,
  url,
  title,
  thumbsOpen,
  onToggleThumbs,
  searchOpen,
  onToggleSearch,
  focusSearchTick,
}: ChromeProps) {
  const { provides: zoom, state: zoomState } = useZoom(documentId)
  const { provides: scroll, state: scrollState } = useScroll(documentId)
  const { provides: rotate } = useRotate(documentId)
  const { provides: search, state: searchState } = useSearch(documentId)
  const { isScrolling } = useViewportScrollActivity(documentId)

  const pct = Math.round((zoomState?.currentZoomLevel ?? 1) * 100)
  const current = scrollState?.currentPage ?? 1
  const total = scrollState?.totalPages ?? 0

  // 頁碼輸入：只在使用者實際編輯時才有草稿值，其餘時間直接顯示目前頁碼。
  // 這樣就不需要「用 effect 把 state 同步到 prop」——那正是本 repo 兩處
  // eslint-disable 的來源，不該再開第三處。
  const [pageDraft, setPageDraft] = useState<string | null>(null)
  const commitPage = () => {
    const n = Number(pageDraft)
    if (Number.isInteger(n) && n >= 1 && n <= total) scroll?.scrollToPage({ pageNumber: n })
    setPageDraft(null)
  }

  const [query, setQuery] = useState('')
  const searchInputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (focusSearchTick > 0) searchInputRef.current?.focus()
  }, [focusSearchTick])

  const hits = searchState.results.length
  const activeHit = searchState.activeResultIndex

  // 開關搜尋要同時開關引擎的搜尋 session：關掉時若不 stopSearch，高亮會留在頁面上。
  useEffect(() => {
    if (!search) return
    if (searchOpen) search.startSearch()
    else search.stopSearch()
  }, [search, searchOpen])

  return (
    <>
      <div className={thumbsOpen ? `${styles.thumbs} ${styles.thumbsOpen}` : styles.thumbs}>
        <ThumbnailsPane documentId={documentId} className={styles.thumbsScroll}>
          {meta => (
            <button
              key={meta.pageIndex}
              type="button"
              className={
                meta.pageIndex + 1 === current
                  ? `${styles.thumbBtn} ${styles.thumbCurrent}`
                  : styles.thumbBtn
              }
              style={{ top: meta.top, height: meta.wrapperHeight }}
              aria-label={`跳至第 ${meta.pageIndex + 1} 頁`}
              onClick={() => scroll?.scrollToPage({ pageNumber: meta.pageIndex + 1 })}
            >
              <span className={styles.thumbImg} style={{ width: meta.width, height: meta.height }}>
                <ThumbImg documentId={documentId} meta={meta} />
              </span>
              <span className={styles.thumbNo}>{meta.pageIndex + 1}</span>
            </button>
          )}
        </ThumbnailsPane>
      </div>

      <div className={isScrolling ? `${styles.island} ${styles.islandDim}` : styles.island}>
        <button
          type="button"
          className={thumbsOpen ? `${styles.btn} ${styles.btnOn}` : styles.btn}
          aria-label="縮圖列"
          aria-pressed={thumbsOpen}
          onClick={onToggleThumbs}
        >
          <Icon name="panel" size={15} />
        </button>
        <button
          type="button"
          className={searchOpen ? `${styles.btn} ${styles.btnOn}` : styles.btn}
          aria-label="搜尋原文"
          aria-pressed={searchOpen}
          onClick={onToggleSearch}
        >
          <Icon name="search" size={15} />
        </button>

        <span className={styles.sep} aria-hidden="true" />

        <button type="button" className={styles.btn} aria-label="縮小" onClick={() => zoom?.zoomOut()}>
          <Icon name="minus" size={15} />
        </button>
        <span className={styles.zoom}>{pct}%</span>
        <button type="button" className={styles.btn} aria-label="放大" onClick={() => zoom?.zoomIn()}>
          <Icon name="plus" size={15} />
        </button>
        <button
          type="button"
          className={styles.btn}
          aria-label="符合寬度"
          onClick={() => zoom?.requestZoom(ZoomMode.FitWidth)}
        >
          <Icon name="arrowsHorizontal" size={15} />
        </button>

        <button
          type="button"
          className={styles.btn}
          aria-label="順時針旋轉 90 度"
          onClick={() => rotate?.rotateForward()}
        >
          <Icon name="refresh" size={15} />
        </button>

        <span className={styles.sep} aria-hidden="true" />

        {/* 頁碼可直接輸入跳頁：原本只有捲動時浮現的唯讀膠囊，長研報要翻到指定頁只能捲。 */}
        <input
          className={styles.pageInput}
          type="text"
          inputMode="numeric"
          value={pageDraft ?? String(current)}
          aria-label={`頁碼，共 ${total} 頁`}
          onChange={e => setPageDraft(e.target.value.replace(/\D/g, ''))}
          onFocus={e => e.currentTarget.select()}
          onBlur={commitPage}
          onKeyDown={e => {
            if (e.key === 'Enter') e.currentTarget.blur()
            if (e.key === 'Escape') {
              setPageDraft(null)
              e.currentTarget.blur()
            }
          }}
        />
        <span className={styles.pageTotal}>/ {total}</span>

        <span className={styles.sep} aria-hidden="true" />

        {/* 下載走原本的檔案端點，不經引擎 —— 使用者要的是券商原檔本身 */}
        <a className={styles.btn} href={url} download aria-label={`下載 ${title}`}>
          <Icon name="download" size={15} />
        </a>
      </div>

      {searchOpen && (
        <div className={styles.searchBar} role="search">
          <Icon name="search" size={14} />
          <input
            ref={searchInputRef}
            className={styles.searchInput}
            type="text"
            placeholder="搜尋研報原文"
            aria-label="搜尋研報原文"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Escape') {
                onToggleSearch()
                return
              }
              if (e.key !== 'Enter') return
              e.preventDefault()
              // 同一組關鍵字按 Enter＝跳下一個命中；換了字才重新全文搜尋。
              if (searchState.query === query && hits > 0) {
                if (e.shiftKey) search?.previousResult()
                else search?.nextResult()
              } else if (query.trim()) {
                search?.searchAllPages(query)
              }
            }}
          />
          <span className={styles.searchCount} aria-live="polite">
            {searchState.loading
              ? '搜尋中…'
              : hits > 0
                ? `${activeHit + 1} / ${hits}`
                : searchState.query === query && query
                  ? '無相符'
                  : ''}
          </span>
          <button
            type="button"
            className={styles.btn}
            aria-label="上一個命中"
            disabled={hits === 0}
            onClick={() => search?.previousResult()}
          >
            <Icon name="chevronDown" size={14} className={styles.flip} />
          </button>
          <button
            type="button"
            className={styles.btn}
            aria-label="下一個命中"
            disabled={hits === 0}
            onClick={() => search?.nextResult()}
          >
            <Icon name="chevronDown" size={14} />
          </button>
          <button type="button" className={styles.btn} aria-label="關閉搜尋" onClick={onToggleSearch}>
            <Icon name="x" size={14} />
          </button>
        </div>
      )}

      <div className={isScrolling ? `${styles.pill} ${styles.pillOn}` : styles.pill} aria-hidden="true">
        {current} / {total}
      </div>
    </>
  )
}

/**
 * 以 EmbedPDF（PDFium/WASM）渲染研報原檔，套用本站自訂的 chrome。
 *
 * **本元件刻意不自理引擎失敗**：`usePdfiumEngine` 的 error 直接往上拋，由 `PdfPane`
 * 的邊界接住並退回瀏覽器內建 iframe。理由是「回退」是 PdfPane 的職責（它同時握有
 * 非 PDF、無檔案等其他分支），把降級決策分散到兩個地方會出現兩套不一致的降級規則。
 *
 * 已知未實作：全文搜尋與命中刻度（`@embedpdf/plugin-search`）。
 */
export default function PdfViewer({ url, title }: Props) {
  // WASM 自架，**刻意不用套件預設的 CDN**：本站在 Cloudflare Tunnel ＋ 登入牆之後，
  // 外連是一個新的失效點，而 WASM 載不到等於整個檢視器起不來。
  // 走 Vite 的 `?url` 讓產物落在 `assets/` —— 那是 `_ImmutableStatic` 已在服務、
  // 且免登入白名單（`/app/assets/`）已涵蓋的路徑；放進 `public/` 會被 SPA 的
  // catch-all 接走並回傳 index.html。
  // fontFallback 必須明確給值：**不給的話引擎會自己套用 jsDelivr CDN 設定**
  // （`fontFallback ?? cdnFontConfig`），等於在登入牆後偷偷開一條外連。理由見 fontFallback.ts。
  // `worker: false` ＝ 走 pdfium-direct-engine（主執行緒），刻意不用預設的 worker 引擎。
  //
  // 2026-07-31 生產實測：worker 路徑會**靜默卡死**——引擎 handle 正常回傳（過了
  // isLoading）、`EmbedPDF` 掛載、DocumentManager 也確實抓了 PDF（伺服器日誌 200），
  // 但 `activeDocumentId` 永遠是 falsy，畫面只剩一個空的 root div。逐項排除過：資產
  // 全 200、wasm 檔正確（`application/wasm`、4.6MB）、PDF 檔本身瀏覽器開得起來、
  // 無 CSP、worker 的 Blob 有帶 `application/javascript`、內嵌 worker 原始碼零 import。
  // 真正的斷點是 **worker 從頭到尾沒有 fetch 過 wasm**（開 DevTools「Disable cache」
  // 多次重載，伺服器日誌一筆 pdfium wasm 請求都沒有）。主執行緒確實送出了
  // `{type:"wasmInit", wasmUrl}`，而 worker 的 `self.onmessage` 對不符條件的訊息
  // **完全靜默、沒有 else 分支**，所以兩端都不會留下任何錯誤。
  //
  // 代價：pdfium 改在主執行緒跑，大檔渲染時會卡住 UI。這是為了先讓讀者看得到研報；
  // 要還原成 worker 只需刪掉這一行，但**還原前請先確認 wasm 真的被抓了**（看伺服器
  // 日誌有沒有 `pdfium-*.wasm` 的請求），否則會回到同一個無訊息的空白畫面。
  const { engine, isLoading, error } = usePdfiumEngine({
    wasmUrl: pdfiumWasmUrl,
    fontFallback: CJK_FONT_FALLBACK,
    worker: false,
  })

  // plugins 需與 url 綁定；每次 render 重建會讓 provider 反覆重載文件。
  const plugins = useMemo(
    () => [
      createPluginRegistration(DocumentManagerPluginPackage, {
        initialDocuments: [{ url }],
      }),
      createPluginRegistration(ViewportPluginPackage, { viewportGap: 18 }),
      createPluginRegistration(ScrollPluginPackage),
      createPluginRegistration(RenderPluginPackage),
      createPluginRegistration(ZoomPluginPackage, { defaultZoomLevel: ZoomMode.FitWidth }),
      // **寬度必須明講**：外掛預設 width 150，而縮圖列只有 96px（見 PdfViewer.module.css
      // 的 .thumbs）且 overflow:hidden——不給值的話每張縮圖左右各被切掉約 27px，
      // 而且不會有任何錯誤。72 ＝ 96 −（6px 細捲軸）−（左右各 ~9px 呼吸空間）。
      // 改動任一邊都要同時改另一邊。
      createPluginRegistration(ThumbnailPluginPackage, { width: THUMB_WIDTH }),
      createPluginRegistration(RotatePluginPackage),
      createPluginRegistration(SearchPluginPackage),
    ],
    [url],
  )

  // 逾時狀態只由計時器寫入、**不在 effect 裡重設**：換 url 時 `timedOutUrl !== url`
  // 自然就是 false，免掉一次 set-state-in-effect（本 repo 對該 lint 規則的兩處豁免
  // 都附了理由，不該再開第三處）。readyRef 是 ref，換 url 時就地重設不觸發渲染。
  const [timedOutUrl, setTimedOutUrl] = useState<string | null>(null)
  // 存「哪一個 url 已就緒」而不是布林旗標，於是**不需要在 effect 裡重設**。
  // 用布林＋重設會壞掉，而且壞得很安靜：React 的子 effect 先於父 effect 執行，
  // 所以 ViewerBody 標記就緒之後，父層 effect 才把旗標清回 false，逾時照樣觸發
  // ——正常載入的研報會在 15 秒後無預警跳成內建檢視。反轉測試抓到的正是這條。
  const readyUrlRef = useRef<string | null>(null)
  // 量測起算點，與逾時同一個 [url] 生命週期。**不在渲染期取值**——react-hooks 的
  // purity／refs 規則會擋，而那確實不安全。0 ＝「還沒設定就已經就緒」，只可能發生在
  // 同一個 commit 內既載入完又就緒（子 effect 先於父 effect）；實務上引擎要數秒、
  // ViewerBody 是好幾個 render 之後才掛上。那種情況寧可不印，也不要印以 0 為基準的
  // 假數字。
  const startRef = useRef(0)
  useEffect(() => {
    startRef.current = performance.now()
    const timer = setTimeout(() => {
      if (readyUrlRef.current !== url) setTimedOutUrl(url)
    }, READY_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [url])
  // 就緒＝`ViewerBody` 掛上（＝文件已載入、頁面看得到），不是「引擎有 handle」——
  // 事故當下引擎正是「有 handle 但文件永遠開不起來」。
  const markReady = useCallback(() => {
    if (readyUrlRef.current === url) return
    readyUrlRef.current = url
    // `worker: false` 之後 pdfium 在主執行緒 instantiate（wasm 4.6MB），這條 log 就是
    // 那個成本的量測點——「開很慢」的客訴不必再靠感覺，直接看數字。只在真正就緒時
    // 印一次，換文件才會再印。
    if (startRef.current > 0) {
      console.info('[pdf] 引擎就緒 %dms', Math.round(performance.now() - startRef.current))
    }
  }, [url])

  if (error) throw error
  // 放在 isLoading 之前：引擎卡在 isLoading 與文件卡在開不起來，兩種都要接住。
  if (timedOutUrl === url) {
    throw new Error(
      `PDF 引擎逾時未就緒（${READY_TIMEOUT_MS}ms）：` +
        `engine=${engine ? 'ready' : 'null'}、isLoading=${isLoading}`,
    )
  }
  if (isLoading || !engine) {
    return (
      <div className={styles.root}>
        <Booting />
      </div>
    )
  }

  return (
    <div className={styles.root}>
      <EmbedPDF engine={engine} plugins={plugins}>
        {({ activeDocumentId }) =>
          activeDocumentId && (
            <DocumentContent documentId={activeDocumentId}>
              {({ isLoaded }) =>
                isLoaded && (
                  <ViewerBody
                    documentId={activeDocumentId}
                    url={url}
                    title={title}
                    onReady={markReady}
                  />
                )
              }
            </DocumentContent>
          )
        }
      </EmbedPDF>
    </div>
  )
}

/** provider 之內的實體：viewport ＋ 頁面 ＋ chrome（hooks 需要 plugin context） */
function ViewerBody({
  documentId,
  url,
  title,
  onReady,
}: {
  documentId: string
  url: string
  title: string
  onReady: () => void
}) {
  // 縮圖列預設收合：側欄 380 ＋ 縮圖 96 ＋ 頁面，在 1280px 以下會開始擠。
  const [thumbsOpen, setThumbsOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [focusSearchTick, setFocusSearchTick] = useState(0)
  const { provides: scroll, state: scrollState } = useScroll(documentId)
  const { provides: zoom } = useZoom(documentId)
  const { provides: rotate } = useRotate(documentId)
  // 本元件掛上就代表文件真的載入了，於是解除上層的逾時。
  useEffect(onReady, [onReady])


  // 鍵盤操作。掛在 window 是安全的：切到「文字」檢視時 ReportPage 會整個卸載
  // PdfPane（見 ReportPage.tsx 的 `view === 'pdf' ? <PdfPane/>`），所以這個監聽
  // 只在 PDF 真的顯示時存在。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      // 正在打字一律讓行——這個畫面上同時有搜尋框與頁碼輸入
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      if (e.altKey) return

      // Ctrl/⌘+F：**刻意攔截瀏覽器的尋找**。PDF 內容是 canvas，不在 DOM 裡，
      // 瀏覽器原生尋找對它完全無效——不攔截等於把使用者導向一個必然失望的功能。
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        setSearchOpen(true)
        setFocusSearchTick(n => n + 1)
        return
      }
      if (e.ctrlKey || e.metaKey) return

      const total = scrollState?.totalPages ?? 0
      const page = scrollState?.currentPage ?? 1
      switch (e.key) {
        case '/':
          e.preventDefault()
          setSearchOpen(true)
          setFocusSearchTick(n => n + 1)
          break
        case 'Escape':
          setSearchOpen(false)
          break
        case 'PageDown':
          e.preventDefault()
          scroll?.scrollToPage({ pageNumber: Math.min(page + 1, total || page + 1) })
          break
        case 'PageUp':
          e.preventDefault()
          scroll?.scrollToPage({ pageNumber: Math.max(page - 1, 1) })
          break
        case 'Home':
          e.preventDefault()
          scroll?.scrollToPage({ pageNumber: 1 })
          break
        case 'End':
          if (total) {
            e.preventDefault()
            scroll?.scrollToPage({ pageNumber: total })
          }
          break
        case '+':
        case '=':
          e.preventDefault()
          zoom?.zoomIn()
          break
        case '-':
          e.preventDefault()
          zoom?.zoomOut()
          break
        case '0':
          e.preventDefault()
          zoom?.requestZoom(ZoomMode.FitWidth)
          break
        case 'r':
        case 'R':
          rotate?.rotateForward()
          break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [scroll, zoom, rotate, scrollState?.currentPage, scrollState?.totalPages])

  return (
    <>
      <Viewport documentId={documentId} className={styles.viewport}>
        <Scroller
          documentId={documentId}
          renderPage={({ width, height, pageIndex }) => (
            <div className={styles.page} style={{ width, height }}>
              {/* Rotate 以 transform 包住頁面內容。紙張外框不必轉——scroll 外掛給的
                  width/height 已依旋轉算好；要轉的是裡面的內容。搜尋高亮必須放在
                  Rotate 之內，否則旋轉後高亮框會留在原座標系、指到錯的位置。 */}
              <Rotate documentId={documentId} pageIndex={pageIndex}>
                <RenderLayer documentId={documentId} pageIndex={pageIndex} />
                <SearchLayer documentId={documentId} pageIndex={pageIndex} />
              </Rotate>
              <span className={styles.pageNo}>{pageIndex + 1}</span>
            </div>
          )}
        />
      </Viewport>
      <Chrome
        documentId={documentId}
        url={url}
        title={title}
        thumbsOpen={thumbsOpen}
        onToggleThumbs={() => setThumbsOpen(v => !v)}
        searchOpen={searchOpen}
        onToggleSearch={() => setSearchOpen(v => !v)}
        focusSearchTick={focusSearchTick}
      />
    </>
  )
}
