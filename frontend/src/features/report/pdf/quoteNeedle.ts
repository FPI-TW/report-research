/**
 * 逐字引文 → PDFium 搜尋關鍵字的階梯。依序試，第一個有命中的即採用。
 *
 * **為什麼需要階梯**：摘錄的 quote 抽自正典文字（`clean_extracted(full_text)`，由 pypdf
 * 抽字後清理），而檢視器搜尋的是 PDFium 現場抽的文字。兩個引擎對空白的處理不同，
 * 原句直接搜有 95.3% 命中（全語料 3,407 條實測），剩下的靠回退層補到 98.6–99.1%。
 *
 * **絕不可把空白全部移除**（實測 72.9% vs 96.1%，倒退 23 個百分點）：PDFium 只在相鄰
 * 字元有一側是 CJK 時才跳過頁面上的空白，ASCII 之間的空白必須在關鍵字裡如實出現。
 * 實測探針：頁面原文「高於 Fed 2%的長期目標」→ 搜 `高於Fed` 命中、搜 `Fed2%` 落空；
 * 純英文 `TV panel prices dropped` 命中、`TVpanelpricesdropped` 落空。
 * 「去掉空白比較保險」的直覺在這裡是反的，會整批打掉英文與中英混排的引文。
 */

/** 第 2 階要用的最短片段長度。太短會match到滿篇都是的字串，歧義率暴增。 */
const MIN_FRAGMENT = 6

/** 第 3 階前綴長度。沿用 app/services/reading/anchor.py 的 PREFIX_STEPS 精神。 */
const PREFIX_LEN = 24

/**
 * 回傳要依序嘗試的關鍵字（已去重、已濾掉空字串）。
 *
 * 1. 折疊空白 —— **這一階必須是折疊版而不是原句**：實測 2.0% 的 quote 內含換行
 *    （`scripts/extract_takeaways.py` 的 `_clean_quote` 刻意只去頭尾空白、內部原樣保留），
 *    而 PDF 頁面上那裡是一個普通空白。12 份 PDF 逐一實測，含 `\n` 的句子 11/12 落空。
 * 2. 引文中最長的無空白連續片段 —— 專治「引文橫跨 PDF 的換行/斷字」。
 * 3. 折疊版的前 24 字元 —— 專治句尾被表格或註腳打斷。
 *
 * 第 2、3 階只有在前一階 0 命中時才會被用到，所以「前綴會提高歧義率」（實測 23.3%
 * vs 6.5%）只影響那約 4% 的尾巴，不影響主體。
 */
export function needleLadder(quote: string): string[] {
  const collapsed = quote.replace(/\s+/g, ' ').trim()
  if (!collapsed) return []

  const longestFragment = collapsed
    .split(' ')
    .reduce((best, part) => (part.length > best.length ? part : best), '')

  const ladder = [
    collapsed,
    longestFragment.length >= MIN_FRAGMENT ? longestFragment : '',
    collapsed.length > PREFIX_LEN ? collapsed.slice(0, PREFIX_LEN) : '',
  ]

  return [...new Set(ladder.filter(Boolean))]
}
