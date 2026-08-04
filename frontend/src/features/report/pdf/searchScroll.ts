/** 搜尋命中的最小形狀（只取捲動需要的欄位，避免綁死外掛的完整型別）。 */
interface SearchHit {
  pageIndex: number
  rects: { origin: { x: number; y: number } }[]
}

interface ScrollLike {
  scrollToPage: (opts: {
    pageNumber: number
    pageCoordinates?: { x: number; y: number }
    alignY?: number
    behavior?: 'auto' | 'smooth'
  }) => void
}

/**
 * 捲到某一個搜尋命中。
 *
 * **`goToResult()` 不會捲動**——它只 dispatch `setActiveResultIndex` 再 emit
 * （`@embedpdf/plugin-search/dist/index.js:386-393`），而整包 `@embedpdf` 沒有任何
 * `onActiveResultChange` 的訂閱者。捲動一定要自己來。工具列既有的「上／下一個命中」
 * 也踩了同一個坑（按了只換高亮顏色、視窗不動），所以那兩處與摘錄跳轉共用這支。
 *
 * **旋轉陷阱**：search 回傳的 rects 已經在正規化座標系（引擎以 `normalizedRotation: true`
 * 開檔，取 r=0），但 `scrollToPage` 的 `pageCoordinates` 會再套一次**內建頁旋轉**
 * （`plugin-scroll` 的 `effectiveRotation = pages[i].rotation + doc.rotation`，而正規化
 * 並沒有把 `pages[i].rotation` 歸零）。兩次旋轉相加會把座標送到頁面外。
 * 所以**內建旋轉不為 0 的頁只給 pageNumber**：捲到頁首，讓高亮自己說話 ——
 * 犧牲精度，不犧牲正確性。使用者按 R 轉的那一項不受影響（那項算得對）。
 */
export function scrollToSearchResult(
  scroll: ScrollLike | null | undefined,
  pageRotation: number | undefined,
  hit: SearchHit | undefined,
  reducedMotion = false,
): void {
  if (!scroll || !hit) return

  const pageNumber = hit.pageIndex + 1
  const behavior = reducedMotion ? 'auto' : 'smooth'
  const origin = hit.rects[0]?.origin

  if (!origin || (pageRotation ?? 0) !== 0) {
    scroll.scrollToPage({ pageNumber, behavior })
    return
  }

  // alignY 是百分比：25 ＝ 命中落在畫面上四分之一處，上方留一點前文
  scroll.scrollToPage({ pageNumber, pageCoordinates: origin, alignY: 25, behavior })
}
