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
import { renderMarkdown } from "/static/app/markdown.js";

let sources = [];   // 最近一次提問的來源清單（供 [n] 對應 report_id 與來源卡片）
let extSources = [];   // 最近一次提問的外部（網路）來源

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

function thinking() {
  $("#askAnswer").innerHTML =
    `<span class="ask-thinking"><span class="spin"></span>檢索研報並思考中…</span>`;
}
// 模型開始上網搜尋時，把思考指示換成「正在搜尋網路…」（僅在尚未出現答案 token 時）
function searchingWeb() {
  const el = $("#askAnswer");
  if (!el.querySelector(".ask-thinking")) return;
  el.innerHTML = `<span class="ask-thinking"><span class="spin"></span>正在搜尋網路補充最新資料…</span>`;
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
}

export async function askQuestion() {
  const input = $("#askInput");
  const q = input.value.trim();
  if (!q) return;
  const my = ++state.askReq;   // 最新者勝
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
  try {
    const resp = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: q,
        market: state.market !== "全部" ? state.market : null,
        instrument_type: state.instrument !== "全部" ? state.instrument : null,
        relates_stock: state.relStock || null,
        relates_futures: state.relFutures || null,
        report_type: state.type !== "全部" ? state.type : null,
      }),
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
    }
  } catch (e) {
    if (my === state.askReq) fail("查詢逾時或失敗，請稍後再試。");
  } finally {
    if (my === state.askReq) $("#askGo").disabled = false;
  }
}
