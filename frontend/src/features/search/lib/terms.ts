/**
 * Tokenises a search query string into unique terms (first-seen order).
 * Phase 2a: simple whitespace split + dedupe.
 * Phase 2b will add CJK bigram expansion to match render.js buildTerms.
 */
export function buildTerms(q: string): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const tok of q.split(/\s+/)) {
    const t = tok.trim()
    if (!t) continue
    if (!seen.has(t)) {
      seen.add(t)
      result.push(t)
    }
  }
  return result
}
