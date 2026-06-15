/*
 * 廷豐研報 共用前端工具（無 build step，以一般 <script src> 載入）。
 * 提供：HTML 跳脫、自動跳脫的 html`` 標籤模板、帶逾時的 fetchJSON。
 * 經典 script 的頂層 const/function 會落在共享的全域環境，後載入的頁面 inline script 可直接取用。
 */

// 同時跳脫引號，讓 esc() 在 HTML「屬性」情境（title=""、data-*）也安全，避免屬性破壞／注入
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// 已信任的 HTML 片段標記：插入 html`` 時不再被跳脫（用於組合子片段或刻意輸出的標記）。
class TrustedHtml {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}
// 把任一插值轉成「可安全插入」的字串：TrustedHtml 原樣輸出、陣列遞迴處理、其餘一律 esc()。
function escVal(v) {
  if (v == null) return "";
  if (v instanceof TrustedHtml) return v.s;
  if (Array.isArray(v)) return v.map(escVal).join("");
  return esc(String(v));
}
// 自動跳脫的標籤模板：html`<span>${userData}</span>` → userData 一律被跳脫，消除「忘了包 esc」這類 XSS。
// 需要插入既有 HTML 片段時，用 raw()/joinHtml() 明確標記為可信。
function html(strings, ...vals) {
  let out = strings[0];
  for (let i = 0; i < vals.length; i++) out += escVal(vals[i]) + strings[i + 1];
  return new TrustedHtml(out);
}
// 明確標記為可信 HTML（只用於程式內、非使用者資料的字串）。
function raw(s) { return new TrustedHtml(String(s ?? "")); }
// 以分隔字串連接一組值（每個值仍依 escVal 規則跳脫），回傳可信片段。
function joinHtml(arr, sep = "") { return new TrustedHtml((arr || []).map(escVal).join(sep)); }

// 帶逾時的 fetch+json：後端無回應時不讓 UI 無限等待。opts 可帶 { ms, cache }。
async function fetchJSON(url, opts = {}) {
  const { ms = 8000, cache } = opts;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  try {
    const init = { signal: ctrl.signal };
    if (cache) init.cache = cache;
    const r = await fetch(url, init);
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}
