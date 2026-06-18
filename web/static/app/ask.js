/*
 * 廷豐研報 前端模組：RAG 問答（聊天式版面：對話可捲、輸入置底）。
 * /api/ask 為 POST + text/event-stream，EventSource 僅支援 GET，故用 fetch reader 手解 SSE。
 * 回答以 markdown 渲染（markdown.js），行內 [n] 引用可點開原始報告 modal。
 */
import { $ } from "/static/app/dom.js";
import { html, raw } from "/static/utils.js";
import { state } from "/static/app/state.js";
import { mLabel, mColor, fmtDate } from "/static/app/meta.js";
import { openFull } from "/static/app/modal.js";
import { confirmDialog } from "/static/app/confirm.js";
import { renderMarkdown } from "/static/app/markdown.js";

let sources = [];   // 最近一次提問的來源清單（供 [n] 對應 report_id 與來源卡片）
let extSources = [];   // 最近一次提問的外部（網路）來源
let currentAskCtrl = null;   // 進行中的 /api/ask 請求；切歷史/重新提問時主動取消

// SSE frame（event:/data: 兩行）→ { event, data }
function parseFrame(frame) {
  let event = "message", data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return null;
  try { return { event, data: JSON.parse(data) }; } catch (e) { return null; }
}

function paintAnswer(answer, streaming) {
  $("#askAnswer").innerHTML =
    renderMarkdown(answer, sources.length) + (streaming ? '<span class="ask-caret"></span>' : "");
}

function paintSources(srcs) {
  const el = $("#askSources");
  if (!srcs.length) { el.innerHTML = ""; return; }
  const rows = srcs.map(s => html`<button class="ask-src" type="button" data-id="${s.report_id}">
      <span class="ask-src-n">${String(s.n)}</span>
      <span class="badge" style="background:${mColor(s.market)}">${mLabel(s.market)}</span>
      <span class="ask-src-main">
        <span class="ask-src-name">${s.file_name}</span>
        ${s.report_date ? html`<span class="ask-src-date">${fmtDate(s.report_date)}</span>` : raw("")}
      </span>
    </button>`).join("");
  el.innerHTML = html`<div class="ask-src-title">引用來源</div>` + rows;
  el.querySelectorAll(".ask-src").forEach(b => b.onclick = () => openFull(b.dataset.id));
}

function safeHttp(u) { return typeof u === "string" && /^https?:\/\//i.test(u); }
function domainOf(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return u; } }

function paintExtSources(srcs) {
  const el = $("#askExtSources");
  const list = (srcs || []).filter(s => safeHttp(s.url));
  if (!list.length) { el.innerHTML = ""; return; }
  el.innerHTML = html`<div class="ask-src-title">外部參考</div>` + list.map(s => html`
    <a class="ask-ext" href="${s.url}" target="_blank" rel="noopener noreferrer">
      <span class="ask-ext-badge">網路</span>
      <span class="ask-ext-main">
        <span class="ask-ext-title">${s.title || s.url}</span>
        <span class="ask-ext-url">${domainOf(s.url)}</span>
      </span>
      <span class="ask-ext-go">${raw(SVG.ext)}</span>
    </a>`).join("");
}

function cancelActiveAsk({ bumpReq = false } = {}) {
  if (bumpReq) state.askReq += 1;   // 讓既有 reader 的 latest-wins 判斷立刻失效
  if (currentAskCtrl) {
    currentAskCtrl.abort();
    currentAskCtrl = null;
  }
  $("#askGo").disabled = false;
}

// 載入側欄歷史問答清單（問答模式常駐，取代原右側抽層）。
// 首次載入才顯示「載入中…」，提問後的刷新沿用既有清單避免閃爍。
export async function loadAskHistory() {
  const list = $("#askHistList");
  if (!list) return;
  if (!list.children.length) list.innerHTML = `<div class="ask-hist-empty">載入中…</div>`;
  try {
    const resp = await fetch("/api/history?limit=50");
    if (resp.status === 401) { window.location.href = "/login"; return; }
    if (!resp.ok) throw new Error("bad");
    renderHistory(await resp.json());
  } catch (e) {
    if (!list.children.length || list.querySelector(".ask-hist-empty"))
      list.innerHTML = `<div class="ask-hist-empty">載入失敗，請稍後再試。</div>`;
  }
}

function renderHistory(items) {
  const list = $("#askHistList");
  if (!items.length) { list.innerHTML = `<div class="ask-hist-empty">尚無歷史問答</div>`; return; }
  // ChatGPT/Gemini 式單行項目：只顯示問題（單行截斷），完整問題放 title 供懸停查看；右上疊刪除鈕（button 不能巢狀）
  list.innerHTML = items.map((it, i) => html`<div class="ask-hist-item">
      <button class="ask-hist-open" type="button" data-i="${String(i)}" title="${it.question}">
        <span class="ask-hist-q">${it.question}</span>
      </button>
      <button class="ask-hist-del" type="button" data-i="${String(i)}" aria-label="刪除此問答" title="刪除此問答">${raw(SVG.trash)}</button>
    </div>`).join("");
  list.querySelectorAll(".ask-hist-open").forEach(b =>
    b.onclick = () => loadHistoryItem(items[parseInt(b.dataset.i, 10)]));
  list.querySelectorAll(".ask-hist-del").forEach(b =>
    b.onclick = () => deleteHistoryItem(items[parseInt(b.dataset.i, 10)], b));
}

// 刪除單筆歷史問答：優先走 DELETE；若代理/舊邊緣對 DELETE 回 404/405，
// 自動回退到 POST alias，成功則即時移除該列；清空回空狀態。
async function deleteHistoryItem(it, btn) {
  if (!it || !it.id || btn.disabled) return;
  const ok = await confirmDialog({
    title: "刪除此問答？",
    body: "將永久移除這筆歷史問答，無法復原。",
    confirmLabel: "刪除",
  });
  if (!ok) return;
  btn.disabled = true;
  try {
    const path = `/api/history/${encodeURIComponent(it.id)}`;
    let resp = await fetch(path, { method: "DELETE" });
    if (resp.status === 404 || resp.status === 405) {
      resp = await fetch(`${path}/delete`, { method: "POST" });
    }
    if (resp.status === 401) { window.location.href = "/login"; return; }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) throw new Error("bad");
    btn.closest(".ask-hist-item")?.remove();
    const list = $("#askHistList");
    if (list && !list.children.length)
      list.innerHTML = `<div class="ask-hist-empty">尚無歷史問答</div>`;
  } catch (e) {
    btn.disabled = false;   // 失敗：復原可再試（不打擾使用者）
  }
}

// 唯讀重現一筆歷史問答（沿用既有渲染；不重打 /api/ask）
function loadHistoryItem(it) {
  cancelActiveAsk({ bumpReq: true });   // 停掉舊串流，避免後續 token 覆寫歷史內容
  $("#askPanel").classList.remove("landing");   // 重現歷史 → 非著陸狀態
  $("#askEmpty").hidden = true;
  $("#askQuestion").hidden = false; $("#askQuestion").textContent = it.question;
  $("#askAnswer").hidden = false;
  sources = it.sources || [];
  extSources = it.ext_sources || [];
  paintSources(sources);
  paintExtSources(extSources);
  paintAnswer(it.answer || "", false);
  paintActions(it.id, it.answer || "", sources.length, extSources.length);
  if (it.feedback) {   // 預先高亮當時回饋（可改）
    const sel = it.feedback === "like" ? "[data-act='like']" : "[data-act='dislike']";
    const btn = document.querySelector("#askActions " + sel);
    if (btn) btn.classList.add("on");
  }
  toBottom();
}

function thinking() {
  $("#askAnswer").innerHTML =
    `<span class="ask-thinking"><span class="spin"></span>檢索研報並思考中…</span>`;
}
// 模型開始上網搜尋時顯示「正在搜尋網路…」。
// 思考階段（尚無答案）→ 取代指示文字；已在串流答案中途搜尋 → 在末尾附一個臨時指示
//（下一個 token 的 paintAnswer 會重繪而自動清掉）。
function searchingWeb() {
  const el = $("#askAnswer");
  if (el.querySelector(".ask-thinking") && !el.querySelector(".ask-searching-inline")) {
    el.innerHTML = `<span class="ask-thinking"><span class="spin"></span>正在搜尋網路補充最新資料…</span>`;
  } else if (!el.querySelector(".ask-searching-inline")) {
    el.insertAdjacentHTML("beforeend",
      `<span class="ask-thinking ask-searching-inline"><span class="spin"></span>正在搜尋網路補充最新資料…</span>`);
    if (nearBottom()) toBottom();
  }
}
function fail(msg) { $("#askAnswer").textContent = msg; }

// 離題拒答：以提示卡渲染（非一般答案泡泡）
function paintNotice(msg) {
  $("#askAnswer").innerHTML = html`<div class="ask-notice">
      <span class="ask-notice-icon" aria-hidden="true">i</span>
      <div class="ask-notice-main">
        <div class="ask-notice-title">無法回答此問題</div>
        <div class="ask-notice-body">${msg}</div>
      </div>
    </div>`;
}

// 動作列圖示（inline SVG，非 emoji）
const SVG = {
  up: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.3a2 2 0 0 0 2-1.7l1.4-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>`,
  down: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.7a2 2 0 0 0-2 1.7l-1.4 9a2 2 0 0 0 2 2.3zm7-13h2.7A2.3 2.3 0 0 1 22 4v7a2.3 2.3 0 0 1-2.3 2H17"/></svg>`,
  copy: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
  chev: `<svg class="ask-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>`,
  ext: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`,
  trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>`,
};

// 重設動作列 + 收合來源（每次新提問）
function resetActions() {
  const a = $("#askActions");
  a.innerHTML = ""; a.hidden = true;
  $("#askSources").classList.remove("open");
  $("#askExtSources").classList.remove("open");
  $("#askExtSources").innerHTML = "";
}

// 回答完成後的 ChatGPT 式動作列：讚/倒讚/複製 +（有來源時）資料來源切換
function paintActions(qaId, answerText, srcCount, extCount) {
  const el = $("#askActions");
  const srcBtn = srcCount
    ? html`<button class="ask-act ask-act-src" data-act="sources" type="button"
        aria-expanded="false" aria-controls="askSources">
        ${raw(SVG.chev)}資料來源 <span class="ask-act-count">${String(srcCount)}</span>
      </button>`
    : raw("");
  const extBtn = extCount
    ? html`<button class="ask-act ask-act-extsrc" data-act="ext" type="button"
        aria-expanded="false" aria-controls="askExtSources">
        ${raw(SVG.chev)}外部參考 <span class="ask-act-count">${String(extCount)}</span>
      </button>`
    : raw("");
  el.innerHTML = html`<button class="ask-act" data-act="like" type="button" title="有幫助" aria-label="讚">${raw(SVG.up)}</button>
    <button class="ask-act" data-act="dislike" type="button" title="沒幫助" aria-label="倒讚">${raw(SVG.down)}</button>
    <button class="ask-act" data-act="copy" type="button" title="複製回答" aria-label="複製回答">${raw(SVG.copy)}</button>
    ${srcBtn}${extBtn}`;
  el.hidden = false;
  el.querySelectorAll(".ask-act").forEach(b => {
    b.onclick = () => onAction(b, qaId, answerText, el);
  });
}

function onAction(btn, qaId, answerText, bar) {
  const act = btn.dataset.act;
  if (act === "like" || act === "dislike") sendFeedback(qaId, act, bar, btn);
  else if (act === "copy") copyText(answerText, btn);
  else if (act === "sources") toggleSources(btn);
  else if (act === "ext") toggleExt(btn);
}

async function sendFeedback(qaId, value, bar, btn) {
  if (!qaId) return;
  // 讚/倒讚互斥：點選者亮起、另一者熄滅
  bar.querySelectorAll("[data-act='like'],[data-act='dislike']")
    .forEach(b => b.classList.toggle("on", b === btn));
  try {
    await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ qa_id: qaId, value }),
    });
  } catch (e) { /* 回饋失敗不打擾使用者 */ }
}

function copyText(text, btn) {
  const ok = () => { btn.classList.add("copied"); setTimeout(() => btn.classList.remove("copied"), 1200); };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(ok).catch(() => fallbackCopy(text, ok));
  } else { fallbackCopy(text, ok); }
}
function fallbackCopy(text, ok) {   // 非安全脈絡（http LAN）的後備複製
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.focus(); ta.select();
  try { document.execCommand("copy"); ok(); } catch (e) { /* ignore */ }
  document.body.removeChild(ta);
}

function toggleSources(btn) {
  const open = $("#askSources").classList.toggle("open");
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  btn.classList.toggle("on", open);
  if (open && nearBottom()) toBottom();
}

function toggleExt(btn) {
  const open = $("#askExtSources").classList.toggle("open");
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  btn.classList.toggle("on", open);
  if (open && nearBottom()) toBottom();
}

// textarea 隨內容增高（上限交給 CSS max-height + overflow）
function autoGrow(el) {
  el.style.height = "auto";
  el.style.height = el.scrollHeight + "px";
}

// 串流時若使用者已在底部則跟著捲到底；往上看舊內容時不打斷
function nearBottom() {
  const t = $("#askThread");
  return t.scrollHeight - t.scrollTop - t.clientHeight < 90;
}
function toBottom() { const t = $("#askThread"); t.scrollTop = t.scrollHeight; }

export function initAsk() {
  const input = $("#askInput");
  $("#askGo").onclick = () => askQuestion();
  input.addEventListener("input", () => autoGrow(input));
  input.addEventListener("keydown", e => {        // Enter 送出、Shift+Enter 換行
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askQuestion(); }
  });
  document.querySelectorAll("#askExamples .ex").forEach(b => b.onclick = () => {
    input.value = b.textContent; autoGrow(input); askQuestion();
  });

  const ans = $("#askAnswer");
  const openCite = target => {
    const a = target.closest && target.closest(".cite");
    if (!a) return;
    const s = sources.find(x => x.n === parseInt(a.dataset.n, 10));
    if (s) openFull(s.report_id);
  };
  ans.addEventListener("click", e => openCite(e.target));
  ans.addEventListener("keydown", e => {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList?.contains("cite")) {
      e.preventDefault(); openCite(e.target);
    }
  });

  $("#askPanel").classList.add("landing");   // 初始：輸入框置中、無底部白色列
}

export async function askQuestion() {
  const input = $("#askInput");
  const q = input.value.trim();
  if (!q) return;
  cancelActiveAsk({ bumpReq: true });
  $("#askPanel").classList.remove("landing");   // 進入對話 → 輸入置底的聊天版面
  const my = state.askReq;   // cancelActiveAsk 已先 bump；此請求拿到新的序號
  $("#askGo").disabled = true;
  $("#askEmpty").hidden = true;
  $("#askQuestion").hidden = false;
  $("#askQuestion").textContent = q;
  $("#askAnswer").hidden = false;
  input.value = ""; autoGrow(input);
  sources = [];
  extSources = [];
  paintSources([]);
  paintExtSources([]);
  resetActions();
  thinking();
  toBottom();
  let answer = "";
  let started = false;
  let notice = false;
  let qaId = null;
  currentAskCtrl = new AbortController();
  try {
    const resp = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // 問答一律檢索全語料：不帶側欄篩選（問答模式側欄已改為歷史清單）
      body: JSON.stringify({ question: q }),
      signal: currentAskCtrl.signal,
    });
    if (resp.status === 401) { window.location.href = "/login"; return; }
    if (!resp.ok || !resp.body) throw new Error("bad response");
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (my !== state.askReq) { reader.cancel(); return; }   // 已被新提問取代
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const evt = parseFrame(buf.slice(0, idx));
        buf = buf.slice(idx + 2);
        if (!evt) continue;
        if (evt.event === "sources") {
          sources = evt.data || [];
          paintSources(sources);
        } else if (evt.event === "status") {
          if (evt.data === "searching_web") searchingWeb();
        } else if (evt.event === "ext_sources") {
          extSources = (evt.data || []).filter(s => s && safeHttp(s.url));
          paintExtSources(extSources);
        } else if (evt.event === "token") {
          started = true;
          answer += evt.data;
          const stick = nearBottom();
          paintAnswer(answer, true);
          if (stick) toBottom();
        } else if (evt.event === "notice") {
          notice = true; started = true;    // 離題提示卡：跳過收尾的 paintAnswer
          paintNotice(evt.data);
          toBottom();
        } else if (evt.event === "done") {
          qaId = (evt.data && evt.data.qa_id) || null;   // 供回饋掛載
        } else if (evt.event === "error") {
          fail("問答服務發生錯誤，請稍後再試。");
          return;
        }
      }
    }
    if (my === state.askReq) {
      if (!notice) paintAnswer(answer, false);   // 收尾：去掉游標（離題卡不可被覆寫）
      if (!started) fail("沒有取得回答，請稍後再試。");
      else if (!notice) paintActions(qaId, answer, sources.length, extSources.length);  // 動作列（離題卡不顯示）
      loadAskHistory();   // 新問答已寫入 qa_log → 刷新側欄歷史清單
    }
  } catch (e) {
    if (my === state.askReq) fail("查詢逾時或失敗，請稍後再試。");
  } finally {
    if (my === state.askReq) currentAskCtrl = null;
    if (my === state.askReq) $("#askGo").disabled = false;
  }
}
