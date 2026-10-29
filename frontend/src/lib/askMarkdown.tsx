import { Fragment, type ReactNode } from 'react'
import { CiteButton } from '../components/CitePreview'
import type { Source } from './askSchemas'

const HTTP = /^https?:\/\//i

export interface RenderAnswerOptions {
  /**
   * 串流中才給：純文字切成小段（CJK 一字一段、英數一詞一段），每段一個帶 key 的 span、套這個 class。
   * key 是該段在行內文字裡的起始位置——串流只會在尾端長字，已出現的段落 key 不變、不重新掛載，
   * 所以 class 上的進場動畫只在新段落出現時播一次。收尾後不給，DOM 回到純文字。
   */
  streamChunkClass?: string
  /** 自訂 [n] 膠囊（預設 CiteButton）。回傳的節點由這裡包上 key。 */
  renderCite?: (n: number, source: Source | undefined, onCite: (n: number) => void) => ReactNode
}

// CJK（含全形標點）一字一段；其餘以「一段非空白＋後綴空白」為一段；開頭的空白自成一段。
const CHUNK_RE = /[\u2E80-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF\u3000-\u303F]|[^\s\u2E80-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF\u3000-\u303F]+\s*|\s+/g

function chunked(text: string, at: number, keyBase: string, cls: string): ReactNode[] {
  return Array.from(text.matchAll(CHUNK_RE), m => (
    <span key={`${keyBase}-c${at + m.index}`} className={cls}>{m[0]}</span>
  ))
}

// 行內：粗體/斜體/行內 code/連結/[n] 膠囊 → React 節點（React 自動轉義文字）
// sources 若提供，[n] 膠囊帶 hover／focus 來源預覽（CiteButton）；未提供則為單純膠囊。
function renderInline(text: string, sourceCount: number, onCite: (n: number) => void, keyBase: string, sources?: Source[], opts?: RenderAnswerOptions): ReactNode[] {
  const out: ReactNode[] = []
  const pushText = (s: string, at: number) => {
    if (!s) return
    if (opts?.streamChunkClass) out.push(<Fragment key={`${keyBase}-t${at}`}>{chunked(s, at, keyBase, opts.streamChunkClass)}</Fragment>)
    else out.push(s)
  }
  const re = /\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)|\[(\d+)\]/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) pushText(text.slice(last, m.index), last)
    const key = `${keyBase}-${i++}`
    if (m[1] !== undefined) out.push(<strong key={key}>{m[1]}</strong>)
    else if (m[2] !== undefined) out.push(<em key={key}>{m[2]}</em>)
    else if (m[3] !== undefined) out.push(<code key={key}>{m[3]}</code>)
    else if (m[4] !== undefined && m[5] !== undefined) {
      const href = m[5].trim()
      if (HTTP.test(href)) out.push(<a key={key} href={href} target="_blank" rel="noopener noreferrer">{m[4]}</a>)
      else pushText(m[0], m.index)
    } else if (m[6] !== undefined) {
      const n = Number(m[6])
      if (n >= 1 && n <= sourceCount) {
        const source = sources?.find(s => s.n === n)
        out.push(opts?.renderCite
          ? <Fragment key={key}>{opts.renderCite(n, source, onCite)}</Fragment>
          : <CiteButton key={key} n={n} source={source} onCite={onCite} />)
      } else pushText(m[0], m.index)
    }
    last = re.lastIndex
  }
  if (last < text.length) pushText(text.slice(last), last)
  return out
}

export function renderAnswer(md: string, sourceCount: number, onCite: (n: number) => void, sources?: Source[], opts?: RenderAnswerOptions): ReactNode {
  const lines = md.split('\n')
  const blocks: ReactNode[] = []
  let para: string[] = []
  let ul: string[] = []
  let ol: string[] = []
  let quote: string[] = []
  let code: string[] | null = null
  let k = 0

  const inline = (text: string, keyBase: string) => renderInline(text, sourceCount, onCite, keyBase, sources, opts)

  const flushPara = () => { if (para.length) { blocks.push(<p key={`p${k++}`}>{inline(para.join(' '), `p${k}`)}</p>); para = [] } }
  const flushUl = () => { if (ul.length) { const items = ul; blocks.push(<ul key={`ul${k++}`}>{items.map((t, i) => <li key={i}>{inline(t, `ul${k}-${i}`)}</li>)}</ul>); ul = [] } }
  const flushOl = () => { if (ol.length) { const items = ol; blocks.push(<ol key={`ol${k++}`}>{items.map((t, i) => <li key={i}>{inline(t, `ol${k}-${i}`)}</li>)}</ol>); ol = [] } }
  const flushQuote = () => { if (quote.length) { const items = quote; blocks.push(<blockquote key={`bq${k++}`}>{inline(items.join(' '), `bq${k}`)}</blockquote>); quote = [] } }
  const flushAll = () => { flushPara(); flushUl(); flushOl(); flushQuote() }

  const splitRow = (s: string): string[] => {
    // 逐字掃描：反斜線跳脫的管線 `\|` 視為字面 |（不當欄位分隔），其餘 | 才切欄。
    const t = s.trim()
    const cells: string[] = []
    let cur = ''
    for (let idx = 0; idx < t.length; idx++) {
      if (t[idx] === '\\' && t[idx + 1] === '|') { cur += '|'; idx += 1; continue }
      if (t[idx] === '|') { cells.push(cur.trim()); cur = ''; continue }
      cur += t[idx]
    }
    cells.push(cur.trim())
    if (cells.length && cells[0] === '') cells.shift()
    if (cells.length && cells[cells.length - 1] === '') cells.pop()
    return cells
  }
  const isTableSep = (s: string): boolean => {
    const t = s.trim()
    if (!t.includes('|') || !t.includes('-')) return false
    const cells = splitRow(t)
    return cells.length > 0 && cells.every(c => /^:?-+:?$/.test(c))
  }
  // 某行是否為「新表格的表頭」（下一行為分隔列）——用於在相鄰表格間正確終止前一表。
  const startsTable = (idx: number): boolean =>
    idx + 1 < lines.length && lines[idx].includes('|') && isTableSep(lines[idx + 1])

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (line.trim().startsWith('```')) {
      if (code === null) { flushAll(); code = [] } else { blocks.push(<pre key={`code${k++}`}><code>{code.join('\n')}</code></pre>); code = null }
      continue
    }
    if (code !== null) { code.push(line); continue }
    // GFM 表格：表頭列 + 分隔列（缺分隔列則不進入表格模式）
    if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      flushAll()
      const headers = splitRow(line)
      i += 1 // 跳過分隔列
      const rows: string[][] = []
      // 吞併資料列至：下一行為空、不含 '|'、本身是分隔列、或是新表格表頭為止（GFM：遇非表格列即結束）。
      while (
        i + 1 < lines.length &&
        lines[i + 1].includes('|') &&
        lines[i + 1].trim() !== '' &&
        !isTableSep(lines[i + 1]) &&
        !startsTable(i + 1)
      ) {
        i += 1
        rows.push(splitRow(lines[i]))
      }
      const tk = k++
      blocks.push(
        <div className="tableWrap" key={`tbl${tk}`}>
          <table>
            <thead><tr>{headers.map((h, j) => <th key={j}>{inline(h, `th${tk}-${j}`)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{headers.map((_, ci) => <td key={ci}>{inline(r[ci] ?? '', `td${tk}-${ri}-${ci}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )
      continue
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line)
    if (h) { flushAll(); const lvl = Math.min(h[1].length, 4); const Tag = (lvl <= 3 ? 'h3' : 'h4') as 'h3' | 'h4'; blocks.push(<Tag key={`h${k++}`} className="tf-md-h">{inline(h[2], `h${k}`)}</Tag>); continue }
    const bq = /^>\s?(.*)$/.exec(line)
    if (bq) { flushPara(); flushUl(); flushOl(); quote.push(bq[1]); continue }
    const uli = /^[-*]\s+(.*)$/.exec(line)
    if (uli) { flushPara(); flushOl(); flushQuote(); ul.push(uli[1]); continue }
    const oli = /^\d+\.\s+(.*)$/.exec(line)
    if (oli) { flushPara(); flushUl(); flushQuote(); ol.push(oli[1]); continue }
    if (line.trim() === '') { flushAll(); continue }
    flushUl(); flushOl(); flushQuote(); para.push(line.trim())
  }
  if (code !== null) blocks.push(<pre key={`code${k++}`}><code>{code.join('\n')}</code></pre>)
  flushAll()
  return <>{blocks}</>
}
