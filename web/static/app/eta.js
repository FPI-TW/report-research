/*
 * 監控面板速率行的純文字格式化（摘要／標註共用）。
 * 無外部 import、無 DOM／瀏覽器相依，故可獨立於瀏覽器外驗證（見 eta.test.mjs）。
 *
 * rateText(remaining, rate, unit)：把「未完成數 remaining、每分鐘速率 rate（unit/分）」
 * 格式化成面板那行的純文字（純文字、無 HTML，無注入風險）。unit 例：「摘要」「標註」。
 *   - rate 為 null（開頁前 8 秒暖機）        → 「速率 計算中…」
 *   - rate < 0.05（顯示四捨五入為 0.0、視同未增長）→ 「速率 0.0 {unit}/分」（不給 ETA）
 *   - remaining <= 0（已跑完）              → 「速率 X.X {unit}/分 · 已完成」
 *   - 其餘                                  → 「速率 X.X {unit}/分 · 預估剩餘 ~N 分／~H.h 時」
 * ETA：mins = remaining / rate；mins < 90 用「分」（四捨五入），否則換算「時」（一位小數）。
 */
export function rateText(remaining, rate, unit) {
  if (rate == null) return "速率 計算中…";
  const line = `速率 ${rate.toFixed(1)} ${unit}/分`;
  if (rate < 0.05) return line; // 顯示為 0.0、視同未增長：不給 ETA
  if (remaining <= 0) return `${line} · 已完成`;
  const mins = remaining / rate;
  const eta = mins < 90 ? `~${Math.round(mins)} 分` : `~${(mins / 60).toFixed(1)} 時`;
  return `${line} · 預估剩餘 ${eta}`;
}

/*
 * ingestRateText(rpm, cps)：導入 INGEST 面板底部速率行。導入無已知總量，故不給 ETA；
 * 第二段改放每秒片段數（cps），與摘要／標註「速率 · 預估剩餘」的兩段結構對齊。
 *   - rpm 為 null（暖機）→「速率 計算中…」
 *   - cps 為 null        →「速率 X.X 篇/分」（只顯示速率）
 *   - 其餘               →「速率 X.X 篇/分 · Y.Y 片段/秒」
 */
export function ingestRateText(rpm, cps) {
  if (rpm == null) return "速率 計算中…";
  const line = `速率 ${rpm.toFixed(1)} 篇/分`;
  return cps == null ? line : `${line} · ${cps.toFixed(1)} 片段/秒`;
}
