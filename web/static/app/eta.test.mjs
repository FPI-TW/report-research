/*
 * eta.js 的零工具鏈單元測試。執行：`node web/static/app/eta.test.mjs`
 * （eta.js 無外部 import，可獨立於瀏覽器外驗證——見其檔頭說明。）
 *
 * 守備：摘要面板那行「速率 X 摘要/分 · 預估剩餘 …」的所有分支，
 * 特別是暖機 null、未增長（0.0 不給 ETA）、已完成、大值換「時」與 90 分邊界。
 */
import { summaryRateText } from "./eta.js";

let pass = 0;
const fails = [];

function eq(name, got, want) {
  if (got === want) pass++;
  else fails.push({ name, got, want });
}

// 暖機中：spm 為 null
eq("warmup-null", summaryRateText(77, null), "速率 計算中…");

// 未增長：spm 恰為 0（管線沒在跑）→ 顯示 0.0、不給 ETA
eq("idle-zero", summaryRateText(77, 0), "速率 0.0 摘要/分");

// 低於門檻：spm < 0.05（顯示四捨五入為 0.0）→ 同樣不給 ETA，避免「0.0 + 巨大 ETA」矛盾
eq("below-threshold", summaryRateText(77, 0.04), "速率 0.0 摘要/分");

// 一般值：remaining 77 / spm 4.2 = 18.33 分 → 四捨五入 ~18 分
eq("normal-minutes", summaryRateText(77, 4.2), "速率 4.2 摘要/分 · 預估剩餘 ~18 分");

// 已完成：remaining 0（仍顯示當下速率）
eq("done-zero-remaining", summaryRateText(0, 4.2), "速率 4.2 摘要/分 · 已完成");

// 大值：1000 / 1 = 1000 分 ≥ 90 → 換算 16.7 時
eq("large-hours", summaryRateText(1000, 1), "速率 1.0 摘要/分 · 預估剩餘 ~16.7 時");

// 邊界（< 90）：89 / 1 = 89 分 → 仍用「分」
eq("boundary-under-90", summaryRateText(89, 1), "速率 1.0 摘要/分 · 預估剩餘 ~89 分");

// 邊界（= 90）：90 / 1 = 90 分 → 非 < 90，改用「時」(1.5 時)
eq("boundary-at-90", summaryRateText(90, 1), "速率 1.0 摘要/分 · 預估剩餘 ~1.5 時");

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
