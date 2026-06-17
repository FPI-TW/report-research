/*
 * 廷豐研報 前端模組：篩選/排序 chip 建構 + 篩選狀態同步。
 */
import { $ } from "/static/app/dom.js";
import { html, raw } from "/static/utils.js";
import { mColor, mLabel, iColor, iLabel, tLabel } from "/static/app/meta.js";
import { state } from "/static/app/state.js";
import { rerun } from "/static/app/api.js";

// 目前生效的篩選維度數（手機收摺鈕與常駐「清除篩選」共用）
export function activeFilterCount() {
  let n = 0;
  if (state.market !== "全部") n++;
  if (state.instrument !== "全部") n++;
  if (state.relStock) n++;
  if (state.relFutures) n++;
  if (state.type !== "全部") n++;
  return n;
}
// 手機收摺鈕：顯示目前生效的篩選數，收起時也看得出有沒有套用篩選
export function updateFilterToggle() {
  const t = $("#filterToggle");
  if (!t) return;
  const n = activeFilterCount();
  t.textContent = n ? `篩選與排序 · ${n}` : "篩選與排序";
}
// 一鍵清除所有篩選（供無結果補救用），同步 chip 視覺與 aria
export function resetFilters() {
  state.market = "全部"; state.instrument = "全部"; state.type = "全部";
  state.relStock = false; state.relFutures = false;
  document.querySelectorAll("#chips .filter").forEach(x => x.classList.toggle("on", x.dataset.m === "全部"));
  document.querySelectorAll("#instrChips .filter").forEach(x => x.classList.toggle("on", x.dataset.i === "全部"));
  document.querySelectorAll("#typeChips .filter").forEach(x => x.classList.toggle("on", x.dataset.rt === "全部"));
  document.querySelectorAll("#subjToggles .filter").forEach(x => x.classList.remove("on"));
  ["#chips", "#instrChips", "#typeChips", "#subjToggles"].forEach(syncPressed);
}

// 篩選 chip 的 aria-pressed 與 .on 視覺狀態同步（事件委派，撐過 innerHTML 重建）
export function syncPressed(sel) {
  document.querySelectorAll(sel + " .filter").forEach(x =>
    x.setAttribute("aria-pressed", x.classList.contains("on")));
}

export function buildChips(markets, total) {
  const all = [{ market: "全部", count: total }, ...markets];
  $("#chips").innerHTML = all.map(m => {
    const isAll = m.market === "全部";
    const dot = isAll ? raw("") : html`<span class="dot" style="background:${mColor(m.market)}"></span>`;
    const nm = isAll ? "全部" : mLabel(m.market);
    return html`<button class="filter${m.market === state.market ? " on" : ""}" data-m="${m.market}" aria-pressed="${m.market === state.market}">
      ${dot}<span class="nm">${nm}</span><span class="ct">${m.count}</span></button>`;
  }).join("");
  $("#chips").querySelectorAll(".filter").forEach(c => c.onclick = () => {
    state.market = c.dataset.m;
    $("#chips").querySelectorAll(".filter").forEach(x => x.classList.toggle("on", x === c));
    rerun();
  });
}
export function buildInstrumentChips(types, total) {
  const all = [{ type: "全部", count: total }, ...types];
  $("#instrChips").innerHTML = all.map(m => {
    const isAll = m.type === "全部";
    const dot = isAll ? raw("") : html`<span class="dot" style="background:${iColor(m.type)}"></span>`;
    const nm = isAll ? "全部" : iLabel(m.type);
    return html`<button class="filter${m.type === state.instrument ? " on" : ""}" data-i="${m.type}" aria-pressed="${m.type === state.instrument}">
      ${dot}<span class="nm">${nm}</span><span class="ct">${m.count}</span></button>`;
  }).join("");
  $("#instrChips").querySelectorAll(".filter").forEach(c => c.onclick = () => {
    state.instrument = c.dataset.i;
    $("#instrChips").querySelectorAll(".filter").forEach(x => x.classList.toggle("on", x === c));
    rerun();
  });
}
export function buildSubjectToggles() {
  const defs = [{ key: "stock", label: "個股" }, { key: "futures", label: "期貨" }];
  $("#subjToggles").innerHTML = defs.map(d =>
    html`<button class="filter${(d.key === "stock" ? state.relStock : state.relFutures) ? " on" : ""}" data-s="${d.key}" aria-pressed="${d.key === "stock" ? state.relStock : state.relFutures}">
      <span class="nm">${d.label}</span></button>`).join("");
  $("#subjToggles").querySelectorAll(".filter").forEach(c => c.onclick = () => {
    const on = c.classList.toggle("on");
    if (c.dataset.s === "stock") state.relStock = on; else state.relFutures = on;
    rerun();
  });
}
export function buildTypeChips(types, total) {
  const all = [{ type: "全部", count: total }, ...types];
  $("#typeChips").innerHTML = all.map(m => {
    const nm = m.type === "全部" ? "全部" : tLabel(m.type);
    return html`<button class="filter${m.type === state.type ? " on" : ""}" data-rt="${m.type}" aria-pressed="${m.type === state.type}">
      <span class="nm">${nm}</span><span class="ct">${m.count}</span></button>`;
  }).join("");
  $("#typeChips").querySelectorAll(".filter").forEach(c => c.onclick = () => {
    state.type = c.dataset.rt;
    $("#typeChips").querySelectorAll(".filter").forEach(x => x.classList.toggle("on", x === c));
    rerun();
  });
}

// 排序 chips：模式相依（search 多一個「相關度」）。若 state.sort 不在當前模式集合
// （只有 search 的 relevance → 切到 browse 會發生）就 reset 成該模式預設後再渲染。
export function buildSortChips(mode) {
  const opts = mode === "search"
    ? [["relevance", "相關度"], ["date_desc", "日期新→舊"], ["date_asc", "日期舊→新"]]
    : [["date_desc", "日期新→舊"], ["date_asc", "日期舊→新"]];
  if (!opts.some(([v]) => v === state.sort)) state.sort = opts[0][0];
  $("#sortChips").innerHTML = opts.map(([v, label]) =>
    html`<button class="filter${v === state.sort ? " on" : ""}" data-sort="${v}" aria-pressed="${v === state.sort}">
      <span class="nm">${label}</span></button>`).join("");
  $("#sortChips").querySelectorAll(".filter").forEach(c => c.onclick = () => {
    state.sort = c.dataset.sort;
    $("#sortChips").querySelectorAll(".filter").forEach(x => x.classList.toggle("on", x === c));
    rerun();
  });
}
