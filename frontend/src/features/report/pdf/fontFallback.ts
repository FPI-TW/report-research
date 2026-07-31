import type { FontFallbackConfig } from '@embedpdf/engines/pdfium'
import { FontCharset } from '@embedpdf/models'
import notoSansHantBold from '@fonts-tc/NotoSansHant-Bold.otf?url'
import notoSansHantRegular from '@fonts-tc/NotoSansHant-Regular.otf?url'

/**
 * 自架的繁體中文備援字型。
 *
 * PDFium 只在**研報自己沒有內嵌該字型**時才會來要備援字；多數券商 PDF 都有內嵌，
 * 所以這條路徑平時完全不會觸發，字型也不會被下載。
 *
 * **這個設定存在的理由是把外連關掉，不只是「加上中文字」。** `usePdfiumEngine` 的
 * `fontFallback` 若不給值，引擎端會套用預設：
 *
 * ```js
 * const effectiveFontFallback = fontFallback === null ? void 0 : fontFallback ?? cdnFontConfig
 * ```
 *
 * 而 `cdnFontConfig` 指向 `cdn.jsdelivr.net`（且是 `buildCdnUrls("latest")`，未鎖版本）。
 * 本站在 Cloudflare Tunnel ＋登入牆之後，外連是一個新的失效點——這正是 WASM 當初
 * 刻意自架的同一個理由。更麻煩的是**失敗是靜默的**：CDN 連不到不會拋錯，只是該頁的
 * 中文變成空白方框，而讀者無從得知自己看到的是殘缺的內容。
 *
 * 走 Vite 的 `?url`，產物落在 `/app/assets/`（`_ImmutableStatic` 已在服務、免登入白名單
 * 已涵蓋、且 immutable 快取一年，同一個權重只會下載一次）。**不可改放 `public/`**：
 * 那條路徑會被 SPA catch-all 接走並回傳 index.html。
 *
 * 字型檔走 `@fonts-tc` 別名而不是 `@embedpdf/fonts-tc/fonts/...`，因為該套件的
 * `exports` 只宣告了 `"."`，深層匯入會被 exports 解析擋掉。別名同時要在
 * `vite.config.ts`、`vitest.config.ts` 與 `tsconfig.json` 三處宣告。
 *
 * **只帶 Regular(400) 與 Bold(700) 兩個字重是刻意的。** 完整 7 個字重共 38 MB，而
 * `FontFallbackManager.selectBestVariant` 依 CSS 字型比對原則挑「最接近的字重」，
 * 缺的字重會落到這兩個之一，不會變成沒有字型。券商研報實務上只用到正常與粗體，
 * 多帶 5 個字重換來的是每次建置與部署多背 27 MB。真的遇到字重明顯不對的研報，
 * 就在下面的陣列補一筆（檔名見 `@embedpdf/fonts-tc` 的 README）。
 *
 * 已知限制：本套件是 Big5／繁體（Noto Sans Hant）。全語料有 63 篇原文為簡體的研報，
 * 其 `GB2312` 字符集沒有對應的備援字型；那需要另一個字型套件，目前刻意不裝。
 */
export const CJK_FONT_FALLBACK: FontFallbackConfig = {
  fonts: {
    [FontCharset.CHINESEBIG5]: [
      { url: notoSansHantRegular, weight: 400 },
      { url: notoSansHantBold, weight: 700 },
    ],
  },
  // 刻意不設 baseUrl：`?url` 產出的是以 "/" 開頭的絕對路徑，而引擎只在 baseUrl 有值
  // 且 url 非絕對時才會前綴，故留空即為原樣使用。
}
