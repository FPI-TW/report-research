/*
 * 廷豐研報 前端模組：URL 狀態同步——讓搜尋／篩選／排序可分享、F5 重整可還原。
 */
import { $ } from "/static/app/dom.js";
import { state, VIEWS, GROUPS } from "/static/app/state.js";
import { updateFilterToggle } from "/static/app/chips.js";
import { toggleClear } from "/static/app/search.js";

export function syncURL() {
  const p = new URLSearchParams();
  const q = $("#q").value.trim();
  if (q) p.set("q", q);
  if (state.market !== "全部") p.set("market", state.market);
  if (state.instrument !== "全部") p.set("instrument", state.instrument);
  if (state.relStock) p.set("stock", "1");
  if (state.relFutures) p.set("futures", "1");
  if (state.type !== "全部") p.set("type", state.type);
  if (state.sort) p.set("sort", state.sort);
  if (state.view !== "grid") p.set("view", state.view);
  if (state.view === "group" && state.group !== "market") p.set("group", state.group);
  const qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
  updateFilterToggle();
}
export function restoreFromURL() {
  const p = new URLSearchParams(location.search);
  if (p.get("market")) state.market = p.get("market");
  if (p.get("instrument")) state.instrument = p.get("instrument");
  state.relStock = p.get("stock") === "1";
  state.relFutures = p.get("futures") === "1";
  if (p.get("type")) state.type = p.get("type");
  if (p.get("sort")) state.sort = p.get("sort");
  if (VIEWS.includes(p.get("view"))) state.view = p.get("view");
  if (GROUPS.includes(p.get("group"))) state.group = p.get("group");
  const q = p.get("q");
  if (q) { $("#q").value = q; toggleClear(); }
  return !!q;
}
