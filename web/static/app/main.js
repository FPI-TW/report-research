/*
 * 廷豐研報 檢索頁 進入點（composition root）：組裝各模組、綁定全域事件、啟動序列。
 * type="module" 為 deferred，於 HTML 解析完成後執行，所有 import 求值完畢後才跑下方程式碼。
 */
import { $, $$ } from "/static/app/dom.js";
import { state, VIEWS } from "/static/app/state.js";
import { resetFilters, syncPressed } from "/static/app/chips.js";
import { syncURL, restoreFromURL } from "/static/app/url.js";
import { paintResults, updateViewBar } from "/static/app/render.js";
import { closeFull } from "/static/app/modal.js";
import { initSearch } from "/static/app/search.js";
import { loadStats, run, loadBrowse, rerun } from "/static/app/api.js";

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
  state.view = b.dataset.view;
  try { localStorage.setItem("rm_view", state.view); } catch (e) {}
  syncURL();
  state.rows.length ? paintResults(false) : updateViewBar();
});
$(".view-switch").addEventListener("keydown", e => {   // radiogroup 方向鍵切換
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  e.preventDefault();
  const idx = VIEWS.indexOf(state.view);
  const next = e.key === "ArrowRight" ? (idx + 1) % VIEWS.length : (idx - 1 + VIEWS.length) % VIEWS.length;
  const btn = $(`.view-switch button[data-view="${VIEWS[next]}"]`);
  btn.click(); btn.focus();
});
$("#groupBy").onchange = () => {
  state.group = $("#groupBy").value;
  syncURL();
  if (state.rows.length && state.view === "group") paintResults(false);
};

// ── 啟動序列 ──
loadStats();
const hadQuery = restoreFromURL();   // 從 URL 還原狀態（可分享／可重整）
$("#groupBy").value = state.group;        // 反映還原後的分組依據
if (hadQuery) run(); else loadBrowse();
