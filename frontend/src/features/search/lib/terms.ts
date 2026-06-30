/**
 * 將查詢字串分詞為唯一 terms（供高亮）。
 * Port of web/static/app/render.js buildTerms：
 *  - CJK token：length===1 收單字；否則收所有相鄰 2-gram（bigram）
 *  - 非 CJK token：length>=2 收整詞
 *  - 去重後依長度由長到短排序（高亮時最長匹配優先）
 */
export function buildTerms(q: string): string[] {
  const terms = new Set<string>()
  for (const tok of q.split(/\s+/)) {
    const t = tok.trim()
    if (!t) continue
    if (/[一-鿿]/.test(t)) {
      if (t.length === 1) terms.add(t)
      for (let i = 0; i < t.length - 1; i++) terms.add(t.slice(i, i + 2))
    } else if (t.length >= 2) {
      terms.add(t)
    }
  }
  return [...terms].sort((a, b) => b.length - a.length)
}
