import { useMemo, useState } from 'react'
import pdfiumWasmUrl from '@embedpdf/pdfium/pdfium.wasm?url'
import { createPluginRegistration } from '@embedpdf/core'
import { EmbedPDF } from '@embedpdf/core/react'
import { usePdfiumEngine } from '@embedpdf/engines/react'
import {
  DocumentContent,
  DocumentManagerPluginPackage,
} from '@embedpdf/plugin-document-manager/react'
import { RenderLayer, RenderPluginPackage } from '@embedpdf/plugin-render/react'
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
}

/**
 * 工具列與縮圖列。
 *
 * 所有控制項都掛在 EmbedPDF provider 之內，因為 useZoom/useScroll 需要 plugin context。
 */
function Chrome({ documentId, url, title, thumbsOpen, onToggleThumbs }: ChromeProps) {
  const { provides: zoom, state: zoomState } = useZoom(documentId)
  const { provides: scroll, state: scrollState } = useScroll(documentId)
  const { isScrolling } = useViewportScrollActivity(documentId)

  const pct = Math.round((zoomState?.currentZoomLevel ?? 1) * 100)
  const current = scrollState?.currentPage ?? 1
  const total = scrollState?.totalPages ?? 0

  return (
    <>
      <div className={thumbsOpen ? `${styles.thumbs} ${styles.thumbsOpen}` : styles.thumbs}>
        <ThumbnailsPane documentId={documentId}>
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

        <span className={styles.sep} aria-hidden="true" />

        {/* 下載走原本的檔案端點，不經引擎 —— 使用者要的是券商原檔本身 */}
        <a className={styles.btn} href={url} download aria-label={`下載 ${title}`}>
          <Icon name="download" size={15} />
        </a>
      </div>

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
      createPluginRegistration(ThumbnailPluginPackage),
    ],
    [url],
  )

  if (error) throw error
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
                  <ViewerBody documentId={activeDocumentId} url={url} title={title} />
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
function ViewerBody({ documentId, url, title }: { documentId: string; url: string; title: string }) {
  // 縮圖列預設收合：側欄 380 ＋ 縮圖 96 ＋ 頁面，在 1280px 以下會開始擠。
  const [thumbsOpen, setThumbsOpen] = useState(false)

  return (
    <>
      <Viewport documentId={documentId} className={styles.viewport}>
        <Scroller
          documentId={documentId}
          renderPage={({ width, height, pageIndex }) => (
            <div className={styles.page} style={{ width, height }}>
              <RenderLayer documentId={documentId} pageIndex={pageIndex} />
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
      />
    </>
  )
}
