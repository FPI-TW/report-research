/*
 * 廷豐研報 前端模組：通用確認框（promise 版，沿用報告 modal 的視覺與進場動畫）。
 * confirmDialog({ title, body, confirmLabel }) → Promise<boolean>
 * 確認 → true；取消鈕／Esc／點背景 → false。含焦點陷阱，焦點預設落在「取消」避免誤觸破壞性操作。
 */
import { $ } from "/static/app/dom.js";

let lastFocused = null;
let resolver = null;

function settle(result) {
  const back = $("#confirmBackdrop");
  if (!back.classList.contains("open")) return;
  back.classList.remove("open");
  document.removeEventListener("keydown", onKey);
  back.removeEventListener("mousedown", onBackdrop);
  if (lastFocused && lastFocused.focus) lastFocused.focus();
  lastFocused = null;
  const r = resolver; resolver = null;
  if (r) r(result);
}

function onKey(e) {
  if (e.key === "Escape") { e.preventDefault(); settle(false); return; }
  if (e.key !== "Tab") return;
  const f = $("#confirmBackdrop").querySelectorAll("button:not([disabled])");
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

function onBackdrop(e) {
  if (e.target === $("#confirmBackdrop")) settle(false);   // 點背景視為取消
}

export function confirmDialog({ title = "確認", body = "", confirmLabel = "確認" } = {}) {
  if (resolver) settle(false);   // 已有開啟中的確認框 → 先取消舊的
  $("#confirmTitle").textContent = title;
  $("#confirmBody").textContent = body;
  const ok = $("#confirmOk"), cancel = $("#confirmCancel");
  ok.textContent = confirmLabel;
  lastFocused = document.activeElement;
  const back = $("#confirmBackdrop");
  back.classList.add("open");
  document.addEventListener("keydown", onKey);
  back.addEventListener("mousedown", onBackdrop);
  ok.onclick = () => settle(true);
  cancel.onclick = () => settle(false);
  cancel.focus();   // 預設焦點放「取消」
  return new Promise((res) => { resolver = res; });
}
