/**
 * 把 text 依 terms 切成 segment 陣列（mark 標示命中）。
 * XSS 安全：只回純資料，由元件以 <mark>{seg.text}</mark> 渲染純節點，
 * 永不產生 HTML 字串（取代 vanilla render.js highlight 的 esc()+字串注入）。
 */
function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function highlightSegments(
  text: string,
  terms: string[],
): { text: string; mark: boolean }[] {
  if (!terms.length || !text) return [{ text, mark: false }]
  const re = new RegExp('(' + terms.map(escapeRegExp).join('|') + ')', 'gi')
  const out: { text: string; mark: boolean }[] = []
  let last = 0
  for (const m of text.matchAll(re)) {
    const i = m.index ?? 0
    if (i > last) out.push({ text: text.slice(last, i), mark: false })
    out.push({ text: m[0], mark: true })
    last = i + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last), mark: false })
  return out.length ? out : [{ text, mark: false }]
}
