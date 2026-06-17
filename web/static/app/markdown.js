/*
 * 廷豐研報 前端模組：極簡、零依賴、XSS 安全的 Markdown → HTML 渲染（供問答回答美化）。
 *
 * 原則：所有文字先 escape，只輸出已知標籤；連結僅允許 http(s)。
 * 支援：標題(#~####)、粗體/斜體、行內碼/程式碼區塊、清單(有序/無序)、表格、
 *       分隔線(---)、引用(>)、段落；行內 [n] 轉成可點引用 chip（程式碼內不轉）。
 * 無 import（自帶 esc），故可獨立於瀏覽器外（node）做單元驗證。
 */

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ESC[c]);
}

// 行內：在「已 escape 的文字」上做替換，只引入已知標籤 → 安全。
function inline(text, maxCite) {
  // 先以反引號切出行內碼，碼內不做任何行內處理／引用
  return text
    .split(/(`[^`]+`)/g)
    .map((seg) => {
      if (seg.length >= 2 && seg.startsWith("`") && seg.endsWith("`")) {
        return "<code>" + esc(seg.slice(1, -1)) + "</code>";
      }
      let s = esc(seg);
      // 連結 [文字](http...)：須在引用之前，避免 [1](url) 被當引用
      s = s.replace(
        /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        (m, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`
      );
      s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
      // 引用 [n]：在來源範圍內才轉 chip，否則維持原樣
      s = s.replace(/\[(\d{1,3})\]/g, (m, d) => {
        const n = parseInt(d, 10);
        return n >= 1 && n <= maxCite
          ? `<a class="cite" data-n="${n}" role="button" tabindex="0" title="查看來源 ${n}">[${n}]</a>`
          : m;
      });
      return s;
    })
    .join("");
}

function splitRow(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

function renderTable(header, rows, maxCite) {
  const th = header.map((c) => `<th>${inline(c, maxCite)}</th>`).join("");
  const trs = rows
    .map(
      (r) =>
        "<tr>" +
        header.map((_, j) => `<td>${inline(r[j] || "", maxCite)}</td>`).join("") +
        "</tr>"
    )
    .join("");
  return `<table class="md-table"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`;
}

function isBlockStart(line) {
  return (
    /^```/.test(line.trim()) ||
    /^#{1,4}\s+/.test(line) ||
    /^\s*[-*•]\s+/.test(line) ||
    /^\s*\d+[.)]\s+/.test(line) ||
    /^\s*>\s?/.test(line) ||
    /^\s*([-*_])\1{2,}\s*$/.test(line)
  );
}

export function renderMarkdown(md, maxCite = 0) {
  const lines = String(md == null ? "" : md).replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  const N = lines.length;
  let i = 0;
  while (i < N) {
    const line = lines[i];

    if (/^```/.test(line.trim())) {           // 程式碼區塊
      const buf = [];
      i++;
      while (i < N && !/^```/.test(lines[i].trim())) buf.push(lines[i++]);
      i++;                                     // 跳過結束的 ```
      out.push("<pre><code>" + esc(buf.join("\n")) + "</code></pre>");
      continue;
    }
    if (!line.trim()) { i++; continue; }       // 空行
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }  // 分隔線

    const h = line.match(/^(#{1,4})\s+(.*)$/);  // 標題
    if (h) {
      const lvl = h[1].length;
      out.push(`<h${lvl}>${inline(h[2].trim(), maxCite)}</h${lvl}>`);
      i++;
      continue;
    }
    if (/^\s*>\s?/.test(line)) {                // 引用區塊
      const buf = [];
      while (i < N && /^\s*>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push("<blockquote>" + inline(buf.join(" "), maxCite) + "</blockquote>");
      continue;
    }
    // 表格：本行含 | 且下一行為分隔列 |---|
    if (
      line.includes("|") &&
      i + 1 < N &&
      lines[i + 1].includes("-") &&
      /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])
    ) {
      const header = splitRow(line);
      i += 2;
      const rows = [];
      while (i < N && lines[i].trim() && lines[i].includes("|")) rows.push(splitRow(lines[i++]));
      out.push(renderTable(header, rows, maxCite));
      continue;
    }
    if (/^\s*[-*•]\s+/.test(line)) {            // 無序清單
      const buf = [];
      while (i < N && /^\s*[-*•]\s+/.test(lines[i])) buf.push(lines[i++].replace(/^\s*[-*•]\s+/, ""));
      out.push("<ul>" + buf.map((it) => `<li>${inline(it, maxCite)}</li>`).join("") + "</ul>");
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {          // 有序清單
      const buf = [];
      while (i < N && /^\s*\d+[.)]\s+/.test(lines[i])) buf.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ""));
      out.push("<ol>" + buf.map((it) => `<li>${inline(it, maxCite)}</li>`).join("") + "</ol>");
      continue;
    }
    // 段落：聚集到空行或下一個區塊起點
    const buf = [line];
    i++;
    while (i < N && lines[i].trim() && !isBlockStart(lines[i])) buf.push(lines[i++]);
    out.push("<p>" + inline(buf.join("\n"), maxCite).replace(/\n/g, "<br>") + "</p>");
  }
  return out.join("");
}
