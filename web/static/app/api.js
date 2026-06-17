/*
 * 廷豐研報 前端模組：資料抓取（stats / browse / search）＋ 重跑派發器。
 */
import { $ } from "/static/app/dom.js";
import { fetchJSON, html, raw } from "/static/utils.js";
import { state, BROWSE_PAGE } from "/static/app/state.js";
import {
  buildChips, buildInstrumentChips, buildSubjectToggles, buildTypeChips,
  buildSortChips, resetFilters,
} from "/static/app/chips.js";
import { syncURL } from "/static/app/url.js";
import { skeleton, render, paintResults, restoreLoadMore } from "/static/app/render.js";

// 篩選/排序變動後，依目前是否有查詢決定重跑搜尋或瀏覽（集中一處，新增維度免到處改）
export function rerun() { $("#q").value.trim() ? run() : loadBrowse(); }

export function renderStats(d) {
  buildChips(d.markets, d.total_reports);
  buildInstrumentChips(d.instrument_types || [], d.total_reports);
  buildSubjectToggles();
  buildTypeChips(d.report_types || [], d.total_reports);
}

export async function loadStats(apply = true) {
  try {
    const d = await fetchJSON("/api/stats");
    if (apply) renderStats(d);
    return d;
  } catch (e) {
    $("#stats").innerHTML = `<span class="pill">後端未連線</span>`;
    return null;
  }
}

export async function loadBrowse(append = false) {
  if (!append) {
    state.offset = 0;
    state.tableSort = { key: null, dir: "asc" };
    buildSortChips("browse");   // 先渲染（含 relevance→date_desc 的 reset），URL 才用到校正後的值
    syncURL();
    skeleton();
  }
  const my = ++state.browseReq;
  try {
    let url = `/api/reports?limit=${BROWSE_PAGE}&offset=${state.offset}`;
    if (state.market !== "全部") url += `&market=${encodeURIComponent(state.market)}`;
    if (state.instrument !== "全部") url += `&instrument_type=${encodeURIComponent(state.instrument)}`;
    if (state.relStock) url += "&relates_stock=true";
    if (state.relFutures) url += "&relates_futures=true";
    if (state.type !== "全部") url += `&report_type=${encodeURIComponent(state.type)}`;
    url += `&sort=${state.sort}`;
    const data = await fetchJSON(url);
    // 最新的 browse 才套用；若此時已有查詢，代表使用者意圖已切到搜尋 → 放棄這次 browse 結果
    if (my !== state.browseReq || $("#q").value.trim()) {
      if (append) restoreLoadMore();   // 還原按鈕，避免被棄用的載入更多卡在「載入中…」
      return;
    }
    state.mode = "browse";
    state.terms = [];
    state.rows = append ? state.rows.concat(data.items || []) : (data.items || []);
    state.total = data.total || 0;
    state.offset = state.rows.length;
    $("#meta").classList.add("show");
    const sortLabel = state.sort === "date_asc" ? "依日期舊到新" : "依日期新到舊";
    $("#meta").textContent = `已導入 ${state.total} 篇報告 — ${sortLabel}`;
    if (!state.total) {
      $("#resultsBar").hidden = true;
      $("#results").className = "";
      const hasFilters = state.market !== "全部" || state.instrument !== "全部"
        || state.type !== "全部" || state.relStock || state.relFutures;
      $("#results").innerHTML = html`<div class="state"><div class="big" aria-hidden="true">🫧</div>
        <div class="msg">這個篩選條件下沒有報告</div>
        ${hasFilters ? html`<div class="examples"><button class="ex" id="browseClear" type="button">清除篩選</button></div>` : raw("")}</div>`;
      const bc = $("#browseClear");
      if (bc) bc.onclick = () => { resetFilters(); loadBrowse(); };
      return;
    }
    paintResults(!append);   // 全量重繪快取結果；append 時不重播進場動畫
  } catch (e) {
    if (my !== state.browseReq || $("#q").value.trim()) return;
    // 載入更多失敗：保留已顯示的結果，只還原按鈕讓使用者重試（不可清空整頁）
    if (append) { restoreLoadMore("載入更多（載入失敗，點擊重試）"); return; }
    $("#results").className = "";
    $("#results").removeAttribute("aria-busy");
    $("#results").innerHTML = html`<div class="state"><div class="big" aria-hidden="true">⚠️</div>
      <div class="msg">載入逾時或失敗，請稍後再試</div>
      <div class="examples"><button class="ex" id="retryBrowse" type="button">重試</button></div></div>`;
    const rb = $("#retryBrowse"); if (rb) rb.onclick = () => loadBrowse();
  }
}

export async function run() {
  const q = $("#q").value.trim();
  if (!q) return;
  state.lastQuery = q;
  const my = ++state.searchReq;
  skeleton();
  buildSortChips("search");
  syncURL();
  try {
    let url = `/api/search?q=${encodeURIComponent(q)}&k=12&passages=4`;
    if (state.market !== "全部") url += `&market=${encodeURIComponent(state.market)}`;
    if (state.instrument !== "全部") url += `&instrument_type=${encodeURIComponent(state.instrument)}`;
    if (state.relStock) url += "&relates_stock=true";
    if (state.relFutures) url += "&relates_futures=true";
    if (state.type !== "全部") url += `&report_type=${encodeURIComponent(state.type)}`;
    url += `&sort=${state.sort}`;
    const data = await fetchJSON(url);
    // 最新的 search 才套用；若查詢已被清空，代表意圖切回瀏覽 → 放棄這次 search 結果
    if (my === state.searchReq && $("#q").value.trim()) render(data);
  } catch (e) {
    if (my === state.searchReq && $("#q").value.trim()) {
      $("#results").className = "";
      $("#results").removeAttribute("aria-busy");
      $("#results").innerHTML = html`<div class="state"><div class="big" aria-hidden="true">⚠️</div>
        <div class="msg">查詢逾時或失敗，請稍後再試</div>
        <div class="examples"><button class="ex" id="retrySearch" type="button">重試</button></div></div>`;
      const rb = $("#retrySearch"); if (rb) rb.onclick = () => run();
    }
  }
}
