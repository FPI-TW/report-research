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

function thinking() {
  $("#askAnswer").innerHTML =
    `<span class="ask-thinking"><span class="spin"></span>檢索研報並思考中…</span>`;
}
function fail(msg) { $("#askAnswer").textContent = msg; }

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
  paintSources([]);
  thinking();
  toBottom();
  let answer = "";
  let started = false;
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
        } else if (evt.event === "token") {
          started = true;
          answer += evt.data;
          const stick = nearBottom();
          paintAnswer(answer, true);
          if (stick) toBottom();
        } else if (evt.event === "error") {
          fail("問答服務發生錯誤，請稍後再試。");
          return;
        }
      }
    }
    if (my === state.askReq) {
      paintAnswer(answer, false);   // 收尾：去掉游標
      if (!started) fail("沒有取得回答，請稍後再試。");
    }
  } catch (e) {
    if (my === state.askReq) fail("查詢逾時或失敗，請稍後再試。");
  } finally {
    if (my === state.askReq) $("#askGo").disabled = false;
  }
}
