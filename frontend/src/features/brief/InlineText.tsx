import type { ReactNode } from 'react'

const INLINE = /\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`/g

/**
 * 簡報條目裡的行內 markdown（粗體、斜體；行內 code 只留文字）。拆成條目之後已不是完整的 markdown 區塊，
 * 不走問答的 renderAnswer（那個會包成段落）。React 會轉義文字，模型輸出不會被當成 HTML。
 */
export function InlineText({ text }: { text: string }) {
  const out: ReactNode[] = []
  let last = 0
  let i = 0
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0
    if (at > last) out.push(text.slice(last, at))
    if (m[1] !== undefined) out.push(<strong key={i++}>{m[1]}</strong>)
    else if (m[2] !== undefined) out.push(<em key={i++}>{m[2]}</em>)
    else out.push(m[3])
    last = at + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return <>{out}</>
}
