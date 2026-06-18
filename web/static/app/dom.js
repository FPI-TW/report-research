/*
 * 廷豐研報 前端模組：DOM 查詢小工具（querySelector 版）。
 * 注意：monitor.html 自有 getElementById 版的 $，不可改用此處的 $。
 */
export const $ = s => document.querySelector(s);
export const $$ = s => document.querySelectorAll(s);

// 一次性「進場」動畫：先移除→強制 reflow→重加，讓重複切換能重播；動畫結束即清除 class。
export function animEnter(el) {
  if (!el) return;
  el.classList.remove("anim-enter");
  void el.offsetWidth;
  el.classList.add("anim-enter");
  el.addEventListener("animationend", () => el.classList.remove("anim-enter"), { once: true });
}

// 方向性「平移進場」：dir = "left" | "right"，新面板從該側滑入＋淡入（檢索↔問答、檢視切換）。
// 同 animEnter 的重播手法；水平溢出由 .content 的 overflow-x:clip 裁掉，避免白邊/橫移。
export function animSlide(el, dir) {
  if (!el) return;
  const cls = dir === "left" ? "slide-from-left" : "slide-from-right";
  el.classList.remove("slide-from-left", "slide-from-right");
  void el.offsetWidth;
  el.classList.add(cls);
  el.addEventListener("animationend", () => el.classList.remove(cls), { once: true });
}
