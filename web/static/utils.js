/*
 * 廷豐研報 共用前端工具（無 build step，原生 ES Module）。
 * 提供：HTML 跳脫、自動跳脫的 html`` 標籤模板、帶逾時的 fetchJSON。
 * 以 export 匯出；各頁以 import 取用（index.html 走 app/ 模組、monitor.html 直接 import）。
 */

// 同時跳脫引號，讓 esc() 在 HTML「屬性」情境（title=""、data-*）也安全，避免屬性破壞／注入
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// 已信任的 HTML 片段標記：插入 html`` 時不再被跳脫（用於組合子片段或刻意輸出的標記）。
export class TrustedHtml {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}
// 把任一插值轉成「可安全插入」的字串：TrustedHtml 原樣輸出、陣列遞迴處理、其餘一律 esc()。
export function escVal(v) {
  if (v == null) return "";
  if (v instanceof TrustedHtml) return v.s;
  if (Array.isArray(v)) return v.map(escVal).join("");
  return esc(String(v));
}
// 自動跳脫的標籤模板：html`<span>${userData}</span>` → userData 一律被跳脫，消除「忘了包 esc」這類 XSS。
// 需要插入既有 HTML 片段時，用 raw()/joinHtml() 明確標記為可信。
export function html(strings, ...vals) {
  let out = strings[0];
  for (let i = 0; i < vals.length; i++) out += escVal(vals[i]) + strings[i + 1];
  return new TrustedHtml(out);
}
// 明確標記為可信 HTML（只用於程式內、非使用者資料的字串）。
export function raw(s) { return new TrustedHtml(String(s ?? "")); }
// 以分隔字串連接一組值（每個值仍依 escVal 規則跳脫），回傳可信片段。
export function joinHtml(arr, sep = "") { return new TrustedHtml((arr || []).map(escVal).join(sep)); }

// 帶逾時的 fetch+json：後端無回應時不讓 UI 無限等待。opts 可帶 { ms, cache }。
export async function fetchJSON(url, opts = {}) {
  const { ms = 8000, cache } = opts;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  try {
    const init = { signal: ctrl.signal };
    if (cache) init.cache = cache;
    const r = await fetch(url, init);
    if (r.status === 401) { window.location.href = "/login"; throw new Error("unauthorized"); }
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}
