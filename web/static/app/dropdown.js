/*
 * 廷豐研報 前端模組：輕量自訂下拉（取代原生 <select>，讓展開的選項清單也能完整客製樣式）。
 * 結構：.gb-select > .gb-trigger(button[aria-haspopup=listbox]) + .gb-menu(ul[role=listbox] > li.gb-opt[role=option])
 * 行為：點選 / Esc / 點外 關閉；Enter·Space·方向鍵·Home·End 鍵盤操作；ARIA 狀態同步。
 * initDropdown(rootSelector, { value, onChange }) → { setValue(v), close(focusBack?) }
 * （setValue 只更新 UI，不觸發 onChange）。
 */
import { $ } from "/static/app/dom.js";

export function initDropdown(rootSel, { value, onChange } = {}) {
  const root = $(rootSel);
  if (!root) return null;
  const trigger = root.querySelector(".gb-trigger");
  const valueEl = root.querySelector(".gb-value");
  const menu = root.querySelector(".gb-menu");
  const opts = [...menu.querySelectorAll(".gb-opt")];
  let isOpen = false;
  let current = value;

  function paint() {
    opts.forEach((o) => o.setAttribute("aria-selected", o.dataset.value === current ? "true" : "false"));
    const sel = opts.find((o) => o.dataset.value === current);
    if (sel) valueEl.textContent = sel.textContent.trim();
  }
  function setValue(v, fire = true) {
    if (v === current) return;   // 同值不重繪、不觸發
    current = v;
    paint();
    if (fire && onChange) onChange(v);
  }
  function open() {
    if (isOpen) return;
    isOpen = true;
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    (opts.find((o) => o.dataset.value === current) || opts[0])?.focus({ preventScroll: true });
  }
  function close(focusBack = true) {
    if (!isOpen && menu.hidden) return;
    isOpen = false;
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (focusBack) trigger.focus();
  }

  trigger.addEventListener("click", () => (isOpen ? close() : open()));
  trigger.addEventListener("keydown", (e) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); open(); }
  });
  opts.forEach((o, i) => {
    o.addEventListener("click", () => { setValue(o.dataset.value); close(); });
    o.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setValue(o.dataset.value); close(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); opts[(i + 1) % opts.length].focus({ preventScroll: true }); }
      else if (e.key === "ArrowUp") { e.preventDefault(); opts[(i - 1 + opts.length) % opts.length].focus({ preventScroll: true }); }
      else if (e.key === "Home") { e.preventDefault(); opts[0].focus({ preventScroll: true }); }
      else if (e.key === "End") { e.preventDefault(); opts[opts.length - 1].focus({ preventScroll: true }); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "Tab") { close(false); }   // Tab 走出時關閉但不搶焦點
    });
  });
  // 點元件外關閉（不把焦點搶回 trigger）
  document.addEventListener("click", (e) => { if (isOpen && !root.contains(e.target)) close(false); });

  paint();   // 初始顯示文字＋aria（不觸發 onChange）
  return {
    setValue: (v) => setValue(v, false),
    close: (focusBack = true) => close(focusBack),
  };
}
