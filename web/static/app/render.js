/*
 * 廷豐研報 前端模組：結果渲染（卡片/列表/表格/分組）＋ 結果區事件綁定。
 */
import { $, $$, animEnter } from "/static/app/dom.js";
import { esc, escRe, html, raw, joinHtml } from "/static/utils.js";
import { mLabel, mColor, iLabel, iColor, tLabel, fmtDate } from "/static/app/meta.js";
import { state } from "/static/app/state.js";
import { resetFilters, activeFilterCount } from "/static/app/chips.js";
import { run, loadBrowse } from "/static/app/api.js";
import { toggleClear } from "/static/app/search.js";
import { openFull } from "/static/app/modal.js";

export function buildTerms(q) {
  const terms = new Set();
  q.split(/\s+/).forEach(tok => {
    const t = tok.trim();
    if (!t) return;
    if (/[一-鿿]/.test(t)) {
      if (t.length === 1) terms.add(t);
      for (let i = 0; i < t.length - 1; i++) terms.add(t.slice(i, i + 2));
    } else if (t.length >= 2) terms.add(t);
  });
  return [...terms].sort((a, b) => b.length - a.length);
}
export function highlight(textRaw, terms) {
  let h = esc(textRaw);
  if (!terms.length) return h;
  const re = new RegExp("(" + terms.map(escRe).join("|") + ")", "gi");
  return h.replace(re, "<mark>$1</mark>");
}

export function skeleton() {
  $("#meta").classList.remove("show");
  $("#resultsBar").hidden = true;
  $("#results").className = "";
  $("#results").setAttribute("aria-busy", "true");   // 讀屏：宣告結果區載入中
  $("#results").innerHTML = Array.from({ length: 4 }, () =>
    `<div class="sk"><div class="sk-line" style="width:40%"></div>
     <div class="sk-line" style="width:92%"></div><div class="sk-line" style="width:76%"></div></div>`).join("");
}

// 標的徽章：base（個股/期貨）+ 標的清單。內嵌最多 3 個，超過顯示「首項 等 N<unit>」，完整清單放 title。
export function subjTag(base, targets, unit) {
  const t = (targets || []).filter(Boolean);
  if (!t.length) return html`<span class="subj">${base}</span>`;
  const full = t.join("、");
  const shown = (unit && t.length > 3) ? `${t[0]} 等 ${t.length}${unit}` : full;
  const titleAttr = t.length > 3 ? html` title="${full}"` : raw("");  // 僅多檔縮寫時才補完整清單
  return html`<span class="subj"${titleAttr}>${base} · ${shown}</span>`;
}

export function render(data) {
  state.rows = data.results || [];
  state.mode = "search";
  state.terms = buildTerms(data.query);
  state.tableSort = { key: null, dir: "asc" };
  $("#meta").classList.add("show");
  const mkt = data.market ? " · " + mLabel(data.market) : "";
  const capped = state.rows.length >= 12;   // k=12，達上限代表只取最相關的前幾篇
  $("#meta").textContent = `「${data.query}」${mkt} — 最相關的 ${state.rows.length} 篇研報`
    + (capped ? "（已達顯示上限，可加關鍵字縮小範圍）" : "");
  if (!state.rows.length) {
    $("#resultsBar").hidden = true;
    $("#results").className = "";
    const hasFilters = state.market !== "全部" || state.instrument !== "全部"
      || state.type !== "全部" || state.relStock || state.relFutures;
    $("#results").innerHTML = html`<div class="state"><div class="big" aria-hidden="true">🫧</div>
      <div class="msg">找不到「${data.query}」的相關研報，換個說法或關鍵字試試</div>
      <div class="examples">
        ${hasFilters ? html`<button class="ex" id="emptyClear" type="button">清除篩選再試</button>` : raw("")}
        <button class="ex" id="emptyBrowse" type="button">瀏覽全部報告</button>
      </div></div>`;
    const clr = $("#emptyClear");
    if (clr) clr.onclick = () => { resetFilters(); run(); };
    $("#emptyBrowse").onclick = () => {
      $("#q").value = ""; toggleClear(); state.lastQuery = ""; resetFilters(); loadBrowse();
    };
    return;
  }
  paintResults(true);
}

// ── 標籤列（商品類型 + 標的）共用 ──
export function tagRowHtml(r) {
  const itags = (r.instrument_types || []).map(t =>
    html`<span class="itag" style="--ic:${iColor(t)}">${iLabel(t)}</span>`);
  const subj = [];
  if (r.relates_stock) subj.push(subjTag("個股", r.stock_targets, "檔"));
  if (r.relates_futures) subj.push(subjTag("期貨", r.futures_targets, ""));
  if (!itags.length && !subj.length) return raw("");
  return html`<div class="tags">${itags}${subj}</div>`;
}

// ── 摘要（卡片/列表共用，純文字無 emoji；點擊展開/收合，見 bindResultEvents）──
export function summaryHtml(r, cls) {
  if (!r.summary) return raw("");
  return html`<span class="${cls} clamp">${r.summary}</span>`;
}

// ── 卡片（grid）：search 顯示排名/片段/相關度條；browse 為精簡版 ──
export function gridCard(r, mode, i) {
  const c = mColor(r.market);
  const info = [];
  if (r.source) info.push(html`<span>${r.source}</span>`);
  if (r.report_date) info.push(html`<span>${fmtDate(r.report_date)}</span>`);
  if (r.report_type) info.push(html`<span>${r.report_type}</span>`);
  if (mode === "search") info.push(html`<span>${r.match_count} 命中片段</span>`);
  const tagRow = tagRowHtml(r);
  let body = raw(""), score = raw(""), rank = raw("");
  // 瀏覽模式整卡可點（不放按鈕，密度更高）；搜尋模式卡片非整卡可點，保留明確 CTA
  let actions = mode === "browse" ? raw("")
    : html`<button class="full-report" data-report-id="${r.report_id}">查看完整報告</button>`;
  if (mode === "search") {
    rank = html`<span class="rank">#${r.rank}</span>`;
    const pct = Math.max(4, Math.min(100, Math.round(r.best_score * 100)));
    const passHtml = (r.passages || []).map((p, pi) =>
      html`<div class="passage${pi > 0 ? " hidden" : ""}">
         <span class="pscore">${Math.round(p.score * 100)}%</span>${raw(highlight(p.content.slice(0, 300), state.terms))}…
       </div>`);
    body = html`<div class="passages">${passHtml}</div>`;
    const moreBtn = (r.passages || []).length > 1
      ? html`<button class="more" data-card="${i}">顯示其他 ${r.passages.length - 1} 段片段</button>` : raw("");
    actions = html`${moreBtn}${actions}`;
    score = html`<div class="score-row" title="分數＝本篇最相關片段與查詢的相似度，非整篇相關度">
        <div class="score-track"><div class="score-fill" data-w="${pct}"></div></div>
        <span class="score-num">${pct}%</span>
      </div>`;
  }
  const browseAttr = mode === "browse" ? html` data-report-id="${r.report_id}"` : raw("");
  return html`<div class="card${mode === "browse" ? " card-click" : ""}" style="animation-delay:${Math.min(i, 12) * 45}ms" data-card="${i}"${browseAttr}>
    <div class="card-top">
      <span class="badge" style="background:${c}">${mLabel(r.market)}</span>
      <span class="fname" title="${r.file_name}">${r.file_name}</span>
      ${rank}
    </div>
    ${info.length ? html`<div class="info">${joinHtml(info, '<span class="dot">·</span>')}</div>` : raw("")}
    ${tagRow}
    ${summaryHtml(r, "summary")}
    ${body}
    ${mode === "search" ? html`<div class="card-actions">${actions}</div>` : raw("")}
    ${score}
  </div>`;
}

// ── 緊湊列表列（list / group 共用），整列可點開完整報告 ──
export function listRow(r, mode) {
  const c = mColor(r.market);
  const meta = [];
  if (r.source) meta.push(r.source);
  if (r.report_date) meta.push(fmtDate(r.report_date));
  if (r.report_type) meta.push(r.report_type);
  if (mode === "search") meta.push(`${r.match_count} 命中`);
  const scorePill = mode === "search"
    ? html`<span class="row-score">${Math.round((r.best_score || 0) * 100)}%</span>` : raw("");
  return html`<div class="row" data-report-id="${r.report_id}" title="${r.file_name}">
    <span class="badge" style="background:${c}">${mLabel(r.market)}</span>
    <span class="row-main">
      <span class="row-name">${r.file_name}</span>
      <span class="row-meta">${joinHtml(meta, '<span class="dot">·</span>')}</span>
      ${summaryHtml(r, "row-summary")}
      ${tagRowHtml(r)}
    </span>
    ${scorePill}
  </div>`;
}

// ── 表格：欄位可點擊做用戶端排序（僅作用於已載入資料）──
export function targetsSummary(r) {
  const parts = [];
  if (r.relates_stock) {
    const t = (r.stock_targets || []).filter(Boolean);
    parts.push(t.length ? `個股 ${t.slice(0, 3).join("、")}${t.length > 3 ? "…" : ""}` : "個股");
  }
  if (r.relates_futures) {
    const t = (r.futures_targets || []).filter(Boolean);
    parts.push(t.length ? `期貨 ${t.slice(0, 3).join("、")}${t.length > 3 ? "…" : ""}` : "期貨");
  }
  return parts.length ? parts.join("　") : "—";   // 純文字，由 html`` 端自動跳脫
}
export function sortedRows(rows) {
  if (!state.tableSort.key) return rows;
  const k = state.tableSort.key, dir = state.tableSort.dir === "asc" ? 1 : -1;
  const val = r => {
    switch (k) {
      case "name": return (r.file_name || "").toLowerCase();
      case "market": return mLabel(r.market);
      case "type": return tLabel(r.report_type);
      case "date": return r.report_date || "";
      case "source": return r.source || "";
      case "score": return r.best_score || 0;
      case "match": return r.match_count || 0;
      default: return "";
    }
  };
  return [...rows].sort((a, b) => {
    const va = val(a), vb = val(b);
    if (typeof va === "number") return (va - vb) * dir;
    return String(va).localeCompare(String(vb), "zh-Hant") * dir;
  });
}
export function tableHtml(rows, mode) {
  const cols = [["name", "報告名稱"], ["market", "市場"], ["type", "類型"],
    ["date", "日期"], ["source", "來源"], [null, "標的"]];
  if (mode === "search") cols.push(["score", "相關度"], ["match", "命中"]);
  const head = cols.map(([key, label]) => {
    if (!key) return html`<th>${label}</th>`;
    const on = state.tableSort.key === key;
    const arrow = on ? (state.tableSort.dir === "asc" ? " ↑" : " ↓") : "";
    const aria = on ? (state.tableSort.dir === "asc" ? "ascending" : "descending") : "none";
    return html`<th class="sortable" data-sort-key="${key}" aria-sort="${aria}">${label}${arrow}</th>`;
  });
  const body = sortedRows(rows).map(r => {
    const cells = [
      html`<td class="t-name" title="${r.summary ? r.file_name + "\n\n" + r.summary : r.file_name}">${r.file_name}</td>`,
      html`<td><span class="badge" style="background:${mColor(r.market)}">${mLabel(r.market)}</span></td>`,
      html`<td>${r.report_type ? tLabel(r.report_type) : "—"}</td>`,
      html`<td class="t-date">${r.report_date ? fmtDate(r.report_date) : "—"}</td>`,
      html`<td>${r.source || "—"}</td>`,
      html`<td class="t-targets">${targetsSummary(r)}</td>`,
    ];
    if (mode === "search") {
      cells.push(html`<td class="t-num">${Math.round((r.best_score || 0) * 100)}%</td>`);
      cells.push(html`<td class="t-num">${r.match_count || 0}</td>`);
    }
    return html`<tr data-report-id="${r.report_id}">${cells}</tr>`;
  });
  return html`<table class="rtable"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

// ── 分組：依市場／報告類型／日期(月) 分組（僅作用於已載入資料）──
export function groupKey(r, by) {
  if (by === "market") return r.market || "—";
  if (by === "report_type") return r.report_type || "—";
  if (by === "month") return r.report_date ? r.report_date.slice(0, 7) : "—";
  return "—";
}
export function groupLabel(key, by) {
  if (key === "—") return "未分類";
  if (by === "market") return mLabel(key);
  if (by === "report_type") return tLabel(key);
  if (by === "month") return key.replace("-", "/");
  return key;
}
export function groupedHtml(rows, mode, by) {
  const groups = new Map();
  rows.forEach(r => {
    const k = groupKey(r, by);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  });
  const entries = [...groups.entries()];
  if (by === "month") entries.sort((a, b) => String(b[0]).localeCompare(String(a[0])));
  else entries.sort((a, b) => b[1].length - a[1].length);
  return joinHtml(entries.map(([k, rs]) => {
    const dot = (by === "market" && k !== "—")
      ? html`<span class="dot" style="background:${mColor(k)}"></span>` : raw("");
    return html`<section class="group">
      <div class="group-head">${dot}<span class="group-name">${groupLabel(k, by)}</span><span class="group-ct">${rs.length}</span></div>
      <div class="group-rows">${rs.map(r => listRow(r, mode))}</div>
    </section>`;
  }));
}

// ── 依目前 state.view 繪製快取結果；切換檢視不重打 API ──
export function paintResults(animate = true) {
  const root = $("#results");
  root.className = "mode-" + state.view + (animate ? "" : " no-rise");
  $("#resultsBar").hidden = false;
  updateViewBar();
  let html;
  if (state.view === "table") html = tableHtml(state.rows, state.mode);
  else if (state.view === "group") html = groupedHtml(state.rows, state.mode, state.group);
  else if (state.view === "list") html = state.rows.map(r => listRow(r, state.mode)).join("");
  else html = state.rows.map((r, i) => gridCard(r, state.mode, i)).join("");
  root.innerHTML = html;
  appendLoadMore();
  bindResultEvents();
  root.removeAttribute("aria-busy");
  markClampable();   // 量測摘要是否真的溢出兩行，溢出才掛「展開」鈕
  if (state.view === "grid" && state.mode === "search") {
    const set = () => $$(".score-fill").forEach(f => f.style.width = f.dataset.w + "%");
    animate ? requestAnimationFrame(set) : set();
  }
}

export function updateViewBar() {
  $$(".view-switch button").forEach(b => {
    const on = b.dataset.view === state.view;
    b.setAttribute("aria-checked", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  });
  $("#groupByWrap").hidden = state.view !== "group";
  const cf = $("#clearFilters");
  if (cf) { const n = activeFilterCount(); cf.classList.toggle("show", !!n); cf.textContent = n ? `清除篩選 · ${n}` : "清除篩選"; }
  let note = "";
  if ((state.view === "table" || state.view === "group")
      && state.mode === "browse" && state.offset < state.total) {
    note = `排序／分組僅套用已載入的 ${state.rows.length} 筆（共 ${state.total.toLocaleString()} 筆，可「載入更多」）`;
  }
  $("#viewNote").textContent = note;
}

export function appendLoadMore() {
  if (state.mode !== "browse" || state.offset >= state.total) return;
  $("#results").insertAdjacentHTML("beforeend",
    `<div id="loadMoreWrap" class="load-more-wrap">
       <button class="more" id="loadMore">載入更多（還有 ${(state.total - state.offset).toLocaleString()} 篇）</button>
     </div>`);
}

// 結果區事件：整列開啟完整報告、展開片段、載入更多、表頭排序
export function bindResultEvents() {
  const root = $("#results");
  root.querySelectorAll("[data-report-id]").forEach(el => {
    el.onclick = () => openFull(el.dataset.reportId);
    // 整列（list/group）、表格列、與瀏覽模式整卡都可用鍵盤開啟
    if (el.matches(".row, tr[data-report-id], .card.card-click")) {
      el.tabIndex = 0;
      el.setAttribute("role", "button");
      el.onkeydown = e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openFull(el.dataset.reportId); }
      };
    }
  });
  root.querySelectorAll(".more[data-card]").forEach(btn => btn.onclick = () => {
    const card = root.querySelector(`.card[data-card="${btn.dataset.card}"]`);
    card.querySelectorAll(".passage.hidden").forEach(p => p.classList.remove("hidden"));
    btn.remove();
  });
  const lm = $("#loadMore");
  if (lm) lm.onclick = e => {
    e.target.disabled = true;
    e.target.textContent = "載入中…";   // 防連點重複請求 + 即時回饋
    loadBrowse(true);
  };
  // 表頭排序：滑鼠 + 鍵盤皆可（aria-sort 已標示，補上實際可操作性）
  root.querySelectorAll("th[data-sort-key]").forEach(th => {
    th.tabIndex = 0;
    th.setAttribute("role", "button");
    const doSort = () => {
      const key = th.dataset.sortKey;
      if (state.tableSort.key === key) state.tableSort.dir = state.tableSort.dir === "asc" ? "desc" : "asc";
      else { state.tableSort.key = key; state.tableSort.dir = "asc"; }
      paintResults(false);
      animEnter($("#results"));
    };
    th.onclick = doSort;
    th.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); doSort(); } };
  });
}

// 量測每則摘要是否真的被截斷（>2 行）；溢出才掛上鍵盤可用的「展開」鈕，
// 避免對只有一兩行的摘要顯示假的展開提示。每次 paint 後重跑（DOM 已重建）。
export function markClampable() {
  $$("#results .summary, #results .row-summary").forEach(el => {
    if (el.scrollHeight <= el.clientHeight + 1) return;   // 沒溢出 → 不需要展開鈕
    el.classList.add("clampable");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "sum-toggle";
    btn.textContent = "展開";
    btn.setAttribute("aria-expanded", "false");
    btn.onclick = e => {
      e.stopPropagation();   // 在可點整列/整卡時不觸發開啟報告
      const open = el.classList.toggle("expanded");
      btn.textContent = open ? "收合" : "展開";
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    };
    el.insertAdjacentElement("afterend", btn);
  });
}

// 還原「載入更多」鈕（被點擊後會 disable + 改字）；用於 append 早退/失敗時避免卡在「載入中…」
export function restoreLoadMore(label) {
  const lm = $("#loadMore");
  if (!lm) return;
  lm.disabled = false;
  lm.textContent = label || `載入更多（還有 ${(state.total - state.offset).toLocaleString()} 篇）`;
}
