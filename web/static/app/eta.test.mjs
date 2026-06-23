/*
 * eta.js 的零工具鏈單元測試。執行：`node web/static/app/eta.test.mjs`
 * （eta.js 無外部 import，可獨立於瀏覽器外驗證——見其檔頭說明。）
 *
 * 守備：監控面板那行「速率 X {unit}/分 · 預估剩餘 …」的所有分支，
 * 特別是暖機 null、未增長（0.0 不給 ETA）、已完成、大值換「時」與 90 分邊界。
 * unit 由呼叫端帶入（摘要／標註共用同一格式器）。
 */
import { rateText } from "./eta.js";

let pass = 0;
const fails = [];

function eq(name, got, want) {
  if (got === want) pass++;
  else fails.push({ name, got, want });
}

// 暖機中：rate 為 null（unit 無關）
eq("warmup-null", rateText(77, null, "摘要"), "速率 計算中…");

// 未增長：rate 恰為 0（管線沒在跑）→ 顯示 0.0、不給 ETA
eq("idle-zero", rateText(77, 0, "摘要"), "速率 0.0 摘要/分");

// 低於門檻：rate < 0.05（顯示四捨五入為 0.0）→ 同樣不給 ETA，避免「0.0 + 巨大 ETA」矛盾
eq("below-threshold", rateText(77, 0.04, "標註"), "速率 0.0 標註/分");

// 一般值（摘要）：77 / 4.2 = 18.33 分 → ~18 分
eq("normal-summary", rateText(77, 4.2, "摘要"), "速率 4.2 摘要/分 · 預估剩餘 ~18 分");

// 一般值（標註）：60 / 5 = 12 分，單位帶入「標註」
eq("normal-tagging", rateText(60, 5, "標註"), "速率 5.0 標註/分 · 預估剩餘 ~12 分");

// 已完成：remaining 0（仍顯示當下速率）
eq("done-zero-remaining", rateText(0, 4.2, "標註"), "速率 4.2 標註/分 · 已完成");

// 大值：1000 / 1 = 1000 分 ≥ 90 → 換算 16.7 時
eq("large-hours", rateText(1000, 1, "摘要"), "速率 1.0 摘要/分 · 預估剩餘 ~16.7 時");

// 邊界（< 90）：89 / 1 = 89 分 → 仍用「分」
eq("boundary-under-90", rateText(89, 1, "標註"), "速率 1.0 標註/分 · 預估剩餘 ~89 分");

// 邊界（= 90）：90 / 1 = 90 分 → 非 < 90，改用「時」(1.5 時)
eq("boundary-at-90", rateText(90, 1, "摘要"), "速率 1.0 摘要/分 · 預估剩餘 ~1.5 時");

if (fails.length) {
  for (const f of fails) {
    console.error(`FAIL ${f.name}`);
    console.error("  得到:", JSON.stringify(f.got));
    console.error("  預期:", JSON.stringify(f.want));
  }
  console.error(`\n${pass} passed, ${fails.length} failed`);
  process.exit(1);
}
console.log(`${pass} passed, 0 failed`);
