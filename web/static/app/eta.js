/*
 * 監控頁「摘要 SUMMARY」面板速率行的純文字格式化。
 * 無外部 import、無 DOM／瀏覽器相依，故可獨立於瀏覽器外驗證（見 eta.test.mjs）。
 *
 * summaryRateText(remaining, spm)：把「未生成數 remaining、每分鐘產生速率 spm（摘要/分）」
 * 格式化成面板那行的純文字（純文字、無 HTML，無注入風險）。
 *   - spm 為 null（開頁前 8 秒暖機）       → 「速率 計算中…」
 *   - spm < 0.05（顯示四捨五入為 0.0、視同未增長）→ 「速率 0.0 摘要/分」（不給 ETA）
 *   - remaining <= 0（已跑完）            → 「速率 X.X 摘要/分 · 已完成」
 *   - 其餘                                → 「速率 X.X 摘要/分 · 預估剩餘 ~N 分／~H.h 時」
 * ETA：mins = remaining / spm；mins < 90 用「分」（四捨五入），否則換算「時」（一位小數）。
 */
export function summaryRateText(remaining, spm) {
  if (spm == null) return "速率 計算中…";
  const line = `速率 ${spm.toFixed(1)} 摘要/分`;
  if (spm < 0.05) return line; // 顯示為 0.0、視同未增長：不給 ETA
  if (remaining <= 0) return `${line} · 已完成`;
  const mins = remaining / spm;
  const eta = mins < 90 ? `~${Math.round(mins)} 分` : `~${(mins / 60).toFixed(1)} 時`;
  return `${line} · 預估剩餘 ${eta}`;
}
