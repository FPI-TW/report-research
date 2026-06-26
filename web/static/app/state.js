/*
 * 廷豐研報 前端模組：單一狀態物件（使用者意圖＋結果快取）＋ 檢視常數。
 * ESM 單例：所有 import 取得同一個 state 參照，跨模組讀寫一致。
 */

// ── 單一狀態物件：使用者意圖（可分享/可還原）＋ 目前結果快取，集中管理避免多處走樣 ──
export const VIEWS = ["group", "table"];   // 視覺順序：列表（group，預設）→ 表格；group 仍依分組渲染
export const GROUPS = ["month", "market"];   // 分類方式：日期(月，預設)／市場（報告類型已移除）
export const state = {
  // 篩選 / 排序 / 檢視意圖
  market: "全部", instrument: "全部", relStock: false, relFutures: false,
  type: "全部",
  sort: "date_desc",            // 初值對齊首屏瀏覽預設，避免載入時 chip 自跳
  view: "group", group: "month",   // 預設「列表」檢視、分組依據預設「日期(月)」
  tableSort: { key: null, dir: "asc" },
  lastQuery: "",
  // 頂層模式：retrieval（檢索：瀏覽/搜尋）｜ ask（問答）。與下方結果快取用的 mode 區隔。
  uiMode: "retrieval",
  // 目前結果快取：切換檢視時免重打 API（search: results；browse: 累積 items）
  rows: [], mode: "browse", terms: [], total: 0, offset: 0,
  // 各市場全量篇數（取自 /api/stats，依篇數由多到少）：供「市場分組」索引頁顯示
  marketStats: [],
  // 非同步請求序號：search / browse / ask 各自獨立，最新者勝（避免互相干擾）
  searchReq: 0, browseReq: 0, askReq: 0,
};
try { const v = localStorage.getItem("rm_view"); if (VIEWS.includes(v)) state.view = v; } catch (e) {}

// ── 「市場分組」呈現決策（純函式，無 DOM）：避免「全域抓一頁、再依當頁分組」導致每組都不完整。──
// market + 全部  → "index"：列出各市場與全量篇數，點選後 drill-in（先把單一市場顯示完整）
// market + 某市場 → "drill"：只看該市場的清單（沿用既有分頁，載入更多＝載入該市場更多）
// 其餘（日期分組等） → "grouped"：交給一般分組渲染
export function groupViewMode(group, market) {
  if (group !== "market") return "grouped";
  return market === "全部" ? "index" : "drill";
}

// ── 瀏覽模式：無關鍵字時列出全部已導入報告（日期新→舊，分頁）──
export const BROWSE_PAGE = 50;
// ── 搜尋模式：每頁報告數（與瀏覽分頁同步、各自獨立）──
export const SEARCH_PAGE = 50;
