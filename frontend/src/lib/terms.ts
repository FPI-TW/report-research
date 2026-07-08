/** 鏡像 vanilla render.js buildTerms：CJK 切 2-gram、非 CJK ≥2 字，依長度降序（長詞優先匹配）。 */
export function queryTerms(q: string): string[] {
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
