/*
 * 廷豐研報 前端模組：URL 狀態同步——讓搜尋／篩選／排序可分享、F5 重整可還原。
 */
import { $ } from "/static/app/dom.js";
import { state, VIEWS, GROUPS } from "/static/app/state.js";
import { MARKET_META, INSTRUMENT_META } from "/static/app/meta.js";
import { updateFilterToggle } from "/static/app/chips.js";
import { toggleClear } from "/static/app/search.js";

const SEARCH_SORTS = ["relevance", "date_desc", "date_asc"];
const BROWSE_SORTS = ["date_desc", "date_asc"];

function pickAllowed(value, allowed, fallback) {
  return value && allowed.includes(value) ? value : fallback;
}

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
  if (state.view !== "group") p.set("view", state.view);
  if (state.view === "group" && state.group !== "month") p.set("group", state.group);
  const qs = p.toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
  updateFilterToggle();
}
export function restoreFromURL(stats) {
  const p = new URLSearchParams(location.search);
  const q = (p.get("q") || "").trim();
  const markets = stats?.markets?.map(m => m.market).filter(Boolean) || Object.keys(MARKET_META);
  const instruments = stats?.instrument_types?.map(t => t.type).filter(Boolean) || Object.keys(INSTRUMENT_META);
  const reportTypes = stats?.report_types?.map(t => t.type).filter(Boolean) || [];
  const sorts = q ? SEARCH_SORTS : BROWSE_SORTS;
  const defaultSort = q ? SEARCH_SORTS[0] : BROWSE_SORTS[0];

  state.market = pickAllowed(p.get("market"), markets, "全部");
  state.instrument = pickAllowed(p.get("instrument"), instruments, "全部");
  state.relStock = p.get("stock") === "1";
  state.relFutures = p.get("futures") === "1";
  state.type = pickAllowed(p.get("type"), reportTypes, "全部");
  state.sort = pickAllowed(p.get("sort"), sorts, defaultSort);
  if (VIEWS.includes(p.get("view"))) state.view = p.get("view");
  if (GROUPS.includes(p.get("group"))) state.group = p.get("group");
  if (q) { $("#q").value = q; toggleClear(); }
  return !!q;
}
