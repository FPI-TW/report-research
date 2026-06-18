/*
 * 廷豐研報 前端模組：搜尋框互動（debounce / Enter / 清除 / 例子）。
 */
import { $ } from "/static/app/dom.js";
import { state } from "/static/app/state.js";
import { run, loadBrowse } from "/static/app/api.js";

export function toggleClear() { $("#clear").classList.toggle("show", !!$("#q").value); }

let debounceT;
export function initSearch() {
  $("#q").addEventListener("input", () => {
    toggleClear();
    clearTimeout(debounceT);
    const v = $("#q").value.trim();
    if (!v) { if (state.lastQuery) { state.lastQuery = ""; loadBrowse(); } return; }
    if (v.length < 2) return;
    debounceT = setTimeout(() => { if (v !== state.lastQuery) run(); }, 450);
  });
  $("#q").addEventListener("keydown", e => { if (e.key === "Enter") { clearTimeout(debounceT); run(); } });
  $("#clear").onclick = () => { $("#q").value = ""; toggleClear(); state.lastQuery = ""; $("#q").focus(); loadBrowse(); };
}
