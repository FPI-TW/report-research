import type { ReactNode } from 'react'

const LINK_RE = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/
const BOLD_RE = /\*\*([^*]+)\*\*/
const ITALIC_RE = /\*([^*\n]+)\*/
const CITE_RE = /\[(\d{1,3})\]/

type Pat = 'link' | 'bold' | 'italic' | 'cite'
const ORDER: Pat[] = ['link', 'bold', 'italic', 'cite'] // 與 vanilla 序列 replace 同優先序
const RE: Record<Pat, RegExp> = { link: LINK_RE, bold: BOLD_RE, italic: ITALIC_RE, cite: CITE_RE }
// 每種樣式的內層可再套用「較低優先」的樣式（不含自身與更高優先者）
const INNER: Record<Pat, Pat[]> = {
  link: ['bold', 'italic', 'cite'],
  bold: ['italic', 'cite'],
  italic: ['cite'],
  cite: [],
}

let keySeq = 0
function k(): number {
  keySeq += 1
  return keySeq
}

/** 在 text 上，依 pats 優先序找「最早出現」的樣式並節點化；遞迴處理內層與剩餘。 */
function renderPart(text: string, pats: Pat[], maxCite: number, onCite?: (n: number) => void): ReactNode[] {
  let best: { pat: Pat; m: RegExpExecArray } | null = null
  for (const pat of pats) {
    const m = RE[pat].exec(text)
    if (!m) continue
    if (pat === 'cite') {
      const n = parseInt(m[1], 10)
      if (!(n >= 1 && n <= maxCite)) continue // 超範圍：不視為樣式
    }
    if (!best || m.index < best.m.index) best = { pat, m }
  }
  if (!best) return [text]
  const { pat, m } = best
  const out: ReactNode[] = []
  if (m.index > 0) out.push(text.slice(0, m.index))
  const innerPats = pats.filter((p) => INNER[pat].includes(p))
  if (pat === 'link') {
    out.push(
      <a key={k()} href={m[2]} target="_blank" rel="noopener">
        {renderPart(m[1], innerPats, maxCite, onCite)}
      </a>,
    )
  } else if (pat === 'bold') {
    out.push(<strong key={k()}>{renderPart(m[1], innerPats, maxCite, onCite)}</strong>)
  } else if (pat === 'italic') {
    out.push(<em key={k()}>{renderPart(m[1], innerPats, maxCite, onCite)}</em>)
  } else {
    const n = parseInt(m[1], 10)
    out.push(
      <a
        key={k()}
        className="cite"
        role="button"
        tabIndex={0}
        title={`查看來源 ${n}`}
        onClick={() => onCite?.(n)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onCite?.(n)
          }
        }}
      >
        [{n}]
      </a>,
    )
  }
  const rest = text.slice(m.index + m[0].length)
  if (rest) out.push(...renderPart(rest, pats, maxCite, onCite))
  return out
}

/** 行內：先以反引號切出行內碼（碼內不處理），其餘節點化。React 自動 escape 文字節點。 */
export function inline(text: string, maxCite: number, onCite?: (n: number) => void): ReactNode[] {
  const out: ReactNode[] = []
  for (const seg of text.split(/(`[^`]+`)/g)) {
    if (seg.length >= 2 && seg.startsWith('`') && seg.endsWith('`')) {
      out.push(<code key={k()}>{seg.slice(1, -1)}</code>)
    } else if (seg) {
      out.push(...renderPart(seg, ORDER, maxCite, onCite))
    }
  }
  return out
}
