/*
 * state.js 純函式的零工具鏈單元測試。執行：`node web/static/app/state.test.mjs`
 * （只 import state.js——無 DOM 相依，可獨立於瀏覽器外驗證。）
 *
 * 守備：groupViewMode() 決定「市場分組」要呈現哪一種畫面：
 *   - 市場分組 + 未指定市場（全部）→ 市場索引（index）：列出各市場與篇數，點選後 drill-in
 *   - 市場分組 + 已指定某市場       → 該市場清單（drill）
 *   - 其餘（日期分組／非市場）       → 一般分組（grouped）
 * 這支函式是修正「全域抓一頁、再依當頁分組 → 每組都不完整」的路由核心。
 */
import { groupViewMode } from "./state.js";

let pass = 0;
const fails = [];

function eq(name, got, want) {
  if (got === want) pass++;
  else fails.push({ name, got, want });
}

// 市場分組、未指定市場 → 索引頁（避免顯示不完整的台股 + 其他市場混雜在下方）
eq("market-all-index", groupViewMode("market", "全部"), "index");

// 市場分組、已指定市場 → drill 進該市場的清單
eq("market-tw-drill", groupViewMode("market", "TW"), "drill");
eq("market-us-drill", groupViewMode("market", "US"), "drill");

// 日期(月)分組 → 一般分組，無論是否套用市場篩選
eq("month-all-grouped", groupViewMode("month", "全部"), "grouped");
eq("month-tw-grouped", groupViewMode("month", "TW"), "grouped");

// 邊界：未知分組維度 → 退回一般分組（不會誤判為索引/drill）
eq("unknown-grouped", groupViewMode("whatever", "全部"), "grouped");

if (fails.length) {
  console.error(`FAIL ${fails.length}/${pass + fails.length}`);
  for (const f of fails) console.error(`  ✗ ${f.name}: got ${JSON.stringify(f.got)} want ${JSON.stringify(f.want)}`);
  process.exit(1);
}
console.log(`ok — ${pass} passed`);
