/*
 * 廷豐研報 檢索頁 進入點（composition root）：組裝各模組、綁定全域事件、啟動序列。
 * type="module" 為 deferred，於 HTML 解析完成後執行，所有 import 求值完畢後才跑下方程式碼。
 */
import { $, $$, animEnter, animSlide } from "/static/app/dom.js";
import { state, VIEWS } from "/static/app/state.js";
import { resetFilters, syncPressed } from "/static/app/chips.js";
import { syncURL, restoreFromURL } from "/static/app/url.js";
import { paintResults, updateViewBar } from "/static/app/render.js";
import { closeFull } from "/static/app/modal.js";
import { initSearch } from "/static/app/search.js";
import { initAsk, loadAskHistory } from "/static/app/ask.js";
import { loadStats, renderStats, run, loadBrowse, rerun } from "/static/app/api.js";
import { initDropdown } from "/static/app/dropdown.js";

// ── 搜尋框互動（debounce / Enter / 清除 / 例子）──
initSearch();

// ── 完整報告 modal：關閉 / backdrop / Escape ──
$("#modalClose").onclick = closeFull;
$("#modalBackdrop").addEventListener("click", e => { if (e.target === e.currentTarget) closeFull(); });
// Escape 僅在 modal 開啟時才關閉，避免攔截頁面其他 Escape 用途
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && $("#modalBackdrop").classList.contains("open")) closeFull();
});

$("#filterToggle").onclick = () => {
  const open = $("#filterGroups").classList.toggle("open");
  $("#filterToggle").setAttribute("aria-expanded", open ? "true" : "false");
};
// 常駐「清除篩選」：套用篩選時隨時可一鍵還原（不必篩到 0 筆才出現）
const clearFiltersBtn = $("#clearFilters");
if (clearFiltersBtn) clearFiltersBtn.onclick = () => { resetFilters(); rerun(); };

// 篩選 chip 的 aria-pressed 與 .on 視覺狀態同步（事件委派，撐過 innerHTML 重建）
["#chips", "#instrChips", "#subjToggles", "#typeChips", "#sortChips"].forEach(sel =>
  document.querySelector(sel).addEventListener("click", () => syncPressed(sel)));

// ── 檢視切換（卡片／列表／表格／分組）：切換不重打 API，只重繪快取結果 ──
$$(".view-switch button").forEach(b => b.onclick = () => {
  if (state.view === b.dataset.view) return;
  // 依分頁前後決定平移方向：往右分頁→新面板從右滑入，往左→從左
  const dir = VIEWS.indexOf(b.dataset.view) > VIEWS.indexOf(state.view) ? "right" : "left";
  state.view = b.dataset.view;
  try { localStorage.setItem("rm_view", state.view); } catch (e) {}
  syncURL();
  if (state.rows.length) { paintResults(false); animSlide($("#results"), dir); }
  else updateViewBar();
});
$(".view-switch").addEventListener("keydown", e => {   // radiogroup 方向鍵切換
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  e.preventDefault();
  const idx = VIEWS.indexOf(state.view);
  const next = e.key === "ArrowRight" ? (idx + 1) % VIEWS.length : (idx - 1 + VIEWS.length) % VIEWS.length;
  const btn = $(`.view-switch button[data-view="${VIEWS[next]}"]`);
  btn.click(); btn.focus();
});
// 分類方式：自訂下拉（取代原生 select，選項清單可完整客製樣式）
const groupDd = initDropdown("#groupBy", {
  value: state.group,
  onChange: (v) => {
    state.group = v;
    syncURL();
    if (state.rows.length && state.view === "group") { paintResults(false); animEnter($("#results")); }
  },
});

// ── 頂層模式切換（檢索 / 問答）：問答用主區獨立的大型提問框；篩選作為共用範圍 ──
initAsk();
function applyMode(mode) {
  state.uiMode = mode;
  $$(".mode-switch button").forEach(b => {
    const on = b.dataset.mode === mode;
    b.setAttribute("aria-checked", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  });
  const ask = mode === "ask";
  document.body.classList.toggle("ask-mode", ask);   // 觸發聊天式滿版版面（CSS）
  // 問答時：收起側欄搜尋框與範例、隱藏檢索結果區；顯示主區提問面板並聚焦輸入框
  $(".search").hidden = ask;
  $("#askPanel").hidden = !ask;
  $("#results").hidden = ask;
  if (ask) {
    $("#resultsBar").hidden = true;
    $("#meta").classList.remove("show");
    animSlide($("#askPanel"), "right");   // 問答為右分頁 → 新面板從右側平移進場
    loadAskHistory();   // 側欄改顯示歷史問答（取代篩選 chips）
    $("#askInput").focus();
  } else {   // 切回檢索：依目前狀態還原結果區（有快取重繪、有查詢重搜、否則瀏覽）
    if (state.rows.length) { paintResults(false); animSlide($("#results"), "left"); }   // 檢索為左分頁 → 從左側
    else if ($("#q").value.trim()) run();
    else loadBrowse();
  }
}
$$(".mode-switch button").forEach(b => b.onclick = () => {
  if (state.uiMode !== b.dataset.mode) applyMode(b.dataset.mode);
});
$(".mode-switch").addEventListener("keydown", e => {   // radiogroup 方向鍵切換
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  e.preventDefault();
  const next = state.uiMode === "ask" ? "retrieval" : "ask";
  applyMode(next);
  $(`.mode-switch button[data-mode="${next}"]`).focus();
});

// ── 啟動序列 ──
async function bootstrap() {
  // 先拿 stats，再驗證 URL 還原值，避免非法參數先滲進第一個 browse/search request。
  const stats = await loadStats(false);
  const hadQuery = restoreFromURL(stats);
  if (stats) renderStats(stats);
  groupDd?.setValue(state.group);   // 反映（URL/localStorage）還原後的分組依據
  if (hadQuery) run(); else loadBrowse();
}

bootstrap();
