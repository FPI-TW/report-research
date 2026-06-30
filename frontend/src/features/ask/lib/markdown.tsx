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

// ─── 區塊渲染（Task 4） ────────────────────────────────────────────────────

/**
 * normalize: 修正黏行 ATX 標題（CJK 句末後緊接 ## 未換行）。
 * 僅在 ``` 圍欄之外處理，避免改動程式碼區塊內容。
 * 護欄：前字限定 CJK／句末標點，標題標記要求井號後接空白或 CJK，
 *        故 C# / F# / #1 / #2 不受影響。
 */
function normalize(mdSrc: string): string {
  return mdSrc
    .split(/(```[\s\S]*?```)/g)
    .map((seg, idx) =>
      idx % 2 === 1
        ? seg
        : seg.replace(
            /([一-鿿。！？：；、，）】」』.!?:;])[ \t]*(#{1,6}(?:[ \t]+|(?=[一-鿿]))\S)/g,
            '$1\n$2',
          ),
    )
    .join('')
}

function splitRow(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
}

function isBlockStart(line: string): boolean {
  return (
    /^```/.test(line.trim()) ||
    /^#{1,6}(?:[ \t]+|(?=[一-鿿]))/.test(line) ||
    /^\s*[-*•]\s+/.test(line) ||
    /^\s*\d+[.)]\s+/.test(line) ||
    /^\s*>\s?/.test(line) ||
    /^\s*([-*_])\1{2,}\s*$/.test(line)
  )
}

/** 把 inline 結果依「\n」切成多段、段間插 <br/>（對齊 vanilla 段落 .replace(/\n/g,'<br>')）。 */
function withBreaks(text: string, maxCite: number, onCite?: (n: number) => void): ReactNode[] {
  const lines = text.split('\n')
  const out: ReactNode[] = []
  lines.forEach((ln, i) => {
    if (i > 0) out.push(<br key={k()} />)
    out.push(...inline(ln, maxCite, onCite))
  })
  return out
}

export function renderMarkdown(mdSrc: string, maxCite = 0, onCite?: (n: number) => void): ReactNode[] {
  const lines = normalize(String(mdSrc == null ? '' : mdSrc).replace(/\r\n?/g, '\n')).split('\n')
  const out: ReactNode[] = []
  const N = lines.length
  let i = 0
  while (i < N) {
    const line = lines[i]

    if (/^```/.test(line.trim())) {
      const lang = line.trim().slice(3).trim()
      const buf: string[] = []
      i++
      while (i < N && !/^```/.test(lines[i].trim())) buf.push(lines[i++])
      i++
      if (lang === 'chart') {
        let title = ''
        try {
          title = String((JSON.parse(buf.join('\n')) as { title?: unknown }).title || '')
        } catch {
          /* 串流中 JSON 未完 */
        }
        out.push(
          <p key={k()} className="md-chart-ph">
            {title ? `（圖表：${title}）` : '（圖表）'}
          </p>,
        )
      } else if (lang === 'kpi') {
        out.push(
          <p key={k()} className="md-chart-ph">
            （重點數據）
          </p>,
        )
      } else {
        out.push(
          <pre key={k()}>
            <code>{buf.join('\n')}</code>
          </pre>,
        )
      }
      continue
    }

    if (!line.trim()) {
      i++
      continue
    }

    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      out.push(<hr key={k()} />)
      i++
      continue
    }

    const h = line.match(/^(#{1,6})(?:[ \t]+|(?=[一-鿿]))(\S.*)$/)
    if (h) {
      const lvl = Math.min(h[1].length, 4)
      const Tag = `h${lvl}` as 'h1' | 'h2' | 'h3' | 'h4'
      out.push(<Tag key={k()}>{inline(h[2].trim(), maxCite, onCite)}</Tag>)
      i++
      continue
    }

    if (/^\s*>\s?/.test(line)) {
      const buf: string[] = []
      while (i < N && /^\s*>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''))
      out.push(<blockquote key={k()}>{inline(buf.join(' '), maxCite, onCite)}</blockquote>)
      continue
    }

    if (
      line.includes('|') &&
      i + 1 < N &&
      lines[i + 1].includes('-') &&
      /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])
    ) {
      const header = splitRow(line)
      i += 2
      const rows: string[][] = []
      while (i < N && lines[i].trim() && lines[i].includes('|')) rows.push(splitRow(lines[i++]))
      out.push(
        <table key={k()} className="md-table">
          <thead>
            <tr>
              {header.map((c, j) => (
                <th key={j}>{inline(c, maxCite, onCite)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri}>
                {header.map((_, j) => (
                  <td key={j}>{inline(r[j] || '', maxCite, onCite)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      )
      continue
    }

    if (/^\s*[-*•]\s+/.test(line)) {
      const buf: string[] = []
      while (i < N && /^\s*[-*•]\s+/.test(lines[i])) buf.push(lines[i++].replace(/^\s*[-*•]\s+/, ''))
      out.push(
        <ul key={k()}>
          {buf.map((it, li) => (
            <li key={li}>{inline(it, maxCite, onCite)}</li>
          ))}
        </ul>,
      )
      continue
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const buf: string[] = []
      while (i < N && /^\s*\d+[.)]\s+/.test(lines[i])) buf.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ''))
      out.push(
        <ol key={k()}>
          {buf.map((it, li) => (
            <li key={li}>{inline(it, maxCite, onCite)}</li>
          ))}
        </ol>,
      )
      continue
    }

    const buf = [line]
    i++
    while (i < N && lines[i].trim() && !isBlockStart(lines[i])) buf.push(lines[i++])
    out.push(<p key={k()}>{withBreaks(buf.join('\n'), maxCite, onCite)}</p>)
  }
  return out
}
