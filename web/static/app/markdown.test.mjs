/*
 * markdown.js 的零工具鏈單元測試。執行：`node web/static/app/markdown.test.mjs`
 * （markdown.js 無外部 import，故可獨立於瀏覽器外驗證——見其檔頭說明。）
 *
 * 重點守備：模型常把 ATX 標題黏在前句句尾或省略空白，導致原始 ## 文字洩漏到畫面
 * （實際發生於 production：「…需透過網路查詢最新收盤價。## 緯創（3231.TW）最新收盤價」）。
 */
import { renderMarkdown } from "./markdown.js";

let pass = 0;
const fails = [];

function check(name, html, mustInclude = [], mustExclude = []) {
  const missing = mustInclude.filter((s) => !html.includes(s));
  const leaked = mustExclude.filter((s) => html.includes(s));
  if (!missing.length && !leaked.length) {
    pass++;
  } else {
    fails.push({ name, html, missing, leaked });
  }
}

// 黏在句尾的 ## 標題（真實 production case）→ 應斷成獨立標題，且不洩漏原始標記
check(
  "glued-heading-real",
  renderMarkdown("需透過網路查詢最新收盤價。## 緯創（3231.TW）最新收盤價\n\n內文", 0),
  ["<h2>緯創（3231.TW）最新收盤價</h2>", "<p>內文</p>"],
  ["## 緯創", "價。##"]
);

// 黏行且省略井號後空白
check("glued-heading-nospace", renderMarkdown("結論。##小結內容", 0), ["<h2>小結內容</h2>"], ["##小結"]);

// 行首標題（含省略空白接 CJK）仍正常
check("normal-heading", renderMarkdown("## 標題\n內文", 0), ["<h2>標題</h2>"], ["## 標題"]);
check("nospace-heading", renderMarkdown("##標題", 0), ["<h2>標題</h2>"], ["##標題"]);

// h5/h6 夾到 h4（CSS 僅樣式化 h1~h4）
check("h5-clamp", renderMarkdown("##### 深層", 0), ["<h4>深層</h4>"], ["#####"]);
check("h6-clamp", renderMarkdown("###### 更深", 0), ["<h4>更深</h4>"], ["######"]);

// 護欄：#1、C#/F# 不被誤判成標題或被誤切
check("guard-hash-digit", renderMarkdown("第一名是 #1 的選手", 0), ["<p>第一名是 #1 的選手</p>"], ["<h1>", "<h4>"]);
check("guard-csharp", renderMarkdown("我用 C# 與 F# 開發", 0), ["<p>我用 C# 與 F# 開發</p>"], ["<h1>"]);

// 護欄：已正確換行者不被重複插入
check("no-double-break", renderMarkdown("文字。\n\n## 標題", 0), ["<p>文字。</p>", "<h2>標題</h2>"], ["。\n\n\n"]);

// 護欄：程式碼圍欄內的「中文。# 註解」不被改動
check("code-fence-untouched", renderMarkdown("```\n價。# 這是註解\n```", 0), ["<pre><code>價。# 這是註解</code></pre>"], ["<h1>"]);

// chart 圍欄 → 佔位（不洩漏 JSON）
check(
  "chart 圍欄 → 佔位（不洩漏 JSON）",
  renderMarkdown('```chart\n{"type":"bar","title":"各廠營收","x":["A"],"series":[{"name":"營收","values":[10]}]}\n```'),
  ["（圖表：各廠營收）"],
  ['"type"', "values", "<pre>"]
);

// chart 圍欄 JSON 不完整（串流中）→ 仍佔位不洩漏
check(
  "chart 圍欄 JSON 不完整（串流中）→ 仍佔位不洩漏",
  renderMarkdown('```chart\n{"type":"bar","title":"半成'),
  ["（圖表"],
  ['"type"', "<pre>"]
);

// kpi 圍欄 → 佔位（不洩漏 JSON）
check(
  "kpi block becomes placeholder",
  renderMarkdown('```kpi\n{"items":[{"label":"營收","value":"+30%"}]}\n```'),
  ["（重點數據）"],
  ['"items"', "```kpi"]
);

// 回歸：粗體 / 行內碼 / 引用 chip / 清單 / 分隔線 / 表格
check(
  "regression-basics",
  renderMarkdown("**粗** `碼` [1]\n\n- 項目\n\n---\n\n| a | b |\n|---|---|\n| 1 | 2 |", 1),
  ["<strong>粗</strong>", "<code>碼</code>", 'class="cite"', "<ul><li>項目</li></ul>", "<hr>", "md-table"]
);

if (fails.length) {
  for (const f of fails) {
    console.error(`FAIL ${f.name}`);
    if (f.missing.length) console.error("  缺少:", JSON.stringify(f.missing));
    if (f.leaked.length) console.error("  洩漏:", JSON.stringify(f.leaked));
    console.error("  HTML:", f.html);
  }
  console.error(`\n${pass} passed, ${fails.length} failed`);
  process.exit(1);
}
console.log(`${pass} passed, 0 failed`);
