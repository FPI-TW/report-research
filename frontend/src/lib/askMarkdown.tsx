import type { ReactNode } from 'react'

const HTTP = /^https?:\/\//i

// 行內：粗體/斜體/行內 code/連結/[n] 膠囊 → React 節點（React 自動轉義文字）
function renderInline(text: string, sourceCount: number, onCite: (n: number) => void, keyBase: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)|\[(\d+)\]/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const key = `${keyBase}-${i++}`
    if (m[1] !== undefined) out.push(<strong key={key}>{m[1]}</strong>)
    else if (m[2] !== undefined) out.push(<em key={key}>{m[2]}</em>)
    else if (m[3] !== undefined) out.push(<code key={key}>{m[3]}</code>)
    else if (m[4] !== undefined && m[5] !== undefined) {
      const href = m[5].trim()
      if (HTTP.test(href)) out.push(<a key={key} href={href} target="_blank" rel="noopener noreferrer">{m[4]}</a>)
      else out.push(m[0])
    } else if (m[6] !== undefined) {
      const n = Number(m[6])
      if (n >= 1 && n <= sourceCount) {
        out.push(<button key={key} type="button" className="tf-cite" onClick={() => onCite(n)}>{n}</button>)
      } else out.push(m[0])
    }
    last = re.lastIndex
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function renderAnswer(md: string, sourceCount: number, onCite: (n: number) => void): ReactNode {
  const lines = md.split('\n')
  const blocks: ReactNode[] = []
  let para: string[] = []
  let ul: string[] = []
  let ol: string[] = []
  let code: string[] | null = null
  let k = 0

  const flushPara = () => { if (para.length) { blocks.push(<p key={`p${k++}`}>{renderInline(para.join(' '), sourceCount, onCite, `p${k}`)}</p>); para = [] } }
  const flushUl = () => { if (ul.length) { const items = ul; blocks.push(<ul key={`ul${k++}`}>{items.map((t, i) => <li key={i}>{renderInline(t, sourceCount, onCite, `ul${k}-${i}`)}</li>)}</ul>); ul = [] } }
  const flushOl = () => { if (ol.length) { const items = ol; blocks.push(<ol key={`ol${k++}`}>{items.map((t, i) => <li key={i}>{renderInline(t, sourceCount, onCite, `ol${k}-${i}`)}</li>)}</ol>); ol = [] } }
  const flushAll = () => { flushPara(); flushUl(); flushOl() }

  for (const line of lines) {
    if (line.trim().startsWith('```')) {
      if (code === null) { flushAll(); code = [] } else { blocks.push(<pre key={`code${k++}`}><code>{code.join('\n')}</code></pre>); code = null }
      continue
    }
    if (code !== null) { code.push(line); continue }
    const h = /^(#{1,6})\s+(.*)$/.exec(line)
    if (h) { flushAll(); const lvl = Math.min(h[1].length, 4); const Tag = (lvl <= 3 ? 'h3' : 'h4') as 'h3' | 'h4'; blocks.push(<Tag key={`h${k++}`} className="tf-md-h">{renderInline(h[2], sourceCount, onCite, `h${k}`)}</Tag>); continue }
    const uli = /^[-*]\s+(.*)$/.exec(line)
    if (uli) { flushPara(); flushOl(); ul.push(uli[1]); continue }
    const oli = /^\d+\.\s+(.*)$/.exec(line)
    if (oli) { flushPara(); flushUl(); ol.push(oli[1]); continue }
    if (line.trim() === '') { flushAll(); continue }
    flushUl(); flushOl(); para.push(line.trim())
  }
  if (code !== null) blocks.push(<pre key={`code${k++}`}><code>{code.join('\n')}</code></pre>)
  flushAll()
  return <>{blocks}</>
}
