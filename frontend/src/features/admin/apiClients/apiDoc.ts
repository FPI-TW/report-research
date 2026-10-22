/**
 * 對外 API 說明文件（repo 的 `docs/EXTERNAL_API.md`）的解析。
 *
 * 那份 Markdown 是唯一真相：「說明文件」分頁照它渲染、下載給 API 用戶端的也是它，「試打」分頁的
 * 端點與參數表單同樣從它解析出來——新增端點只要改文件，這一頁就跟著有。文件與後端路由的一致性
 * 由 `tests/test_external_api_doc.py` 對帳。
 *
 * 只支援那份文件用到的 Markdown 子集：標題、段落、清單、圍欄程式碼、表格，行內的 `code`、**粗體**
 * 與連結。輸出是資料結構、由 React 元件渲染，不經 innerHTML。
 */

export const BASE_URL_PLACEHOLDER = '{BASE_URL}'

export type Block =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'para'; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'code'; lang: string; text: string }
  | { kind: 'table'; head: string[]; rows: string[][] }

/** 文件裡的站台網址佔位字換成實際網址（後台以目前站台的 origin 代入）。 */
export function withBaseUrl(md: string, baseUrl: string): string {
  return md.split(BASE_URL_PLACEHOLDER).join(baseUrl)
}

const FENCE = /^```(\S*)\s*$/
const HEADING = /^(#{1,4})\s+(.+?)\s*$/
const BULLET = /^\s*-\s+(.*)$/
const ORDERED = /^\s*\d+\.\s+(.*)$/

const isTableRow = (line: string) => line.trimStart().startsWith('|')
const cellsOf = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(s => s.trim())
const isBlockStart = (line: string) =>
  FENCE.test(line) || HEADING.test(line) || BULLET.test(line) || ORDERED.test(line) || isTableRow(line)

export function parseMarkdown(md: string): Block[] {
  const lines = md.replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { i++; continue }
    const fence = FENCE.exec(line)
    if (fence) {
      const body: string[] = []
      i++
      while (i < lines.length && !/^```\s*$/.test(lines[i])) body.push(lines[i++])
      i++ // 收尾的 ```
      blocks.push({ kind: 'code', lang: fence[1], text: body.join('\n') })
      continue
    }
    const heading = HEADING.exec(line)
    if (heading) {
      blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2] })
      i++
      continue
    }
    if (isTableRow(line)) {
      const rows: string[][] = []
      while (i < lines.length && isTableRow(lines[i])) rows.push(cellsOf(lines[i++]))
      // 第二列是 |---|---| 分隔列
      const body = rows.slice(1).filter(r => !r.every(c => /^:?-+:?$/.test(c)))
      blocks.push({ kind: 'table', head: rows[0], rows: body })
      continue
    }
    const ordered = ORDERED.test(line)
    if (ordered || BULLET.test(line)) {
      const marker = ordered ? ORDERED : BULLET
      const items: string[] = []
      while (i < lines.length && lines[i].trim()) {
        const m = marker.exec(lines[i])
        if (m) items.push(m[1])
        else if (/^\s+\S/.test(lines[i]) && items.length) items[items.length - 1] += ' ' + lines[i].trim()
        else break
        i++
      }
      blocks.push({ kind: 'list', ordered, items })
      continue
    }
    const para: string[] = []
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) para.push(lines[i++].trim())
    blocks.push({ kind: 'para', text: para.join(' ') })
  }
  return blocks
}

export type Inline =
  | { kind: 'text'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'strong'; text: string }
  | { kind: 'link'; text: string; href: string }

const INLINE = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g

/** 行內標記。連結只接受 http(s)：文件會交給外部，不讓奇怪的 scheme 混進來。 */
export function parseInline(text: string): Inline[] {
  const out: Inline[] = []
  let last = 0
  for (const m of text.matchAll(INLINE)) {
    if (m.index > last) out.push({ kind: 'text', text: text.slice(last, m.index) })
    if (m[1] != null) out.push({ kind: 'code', text: m[1] })
    else if (m[2] != null) out.push({ kind: 'strong', text: m[2] })
    else if (/^https?:\/\//.test(m[4])) out.push({ kind: 'link', text: m[3], href: m[4] })
    else out.push({ kind: 'text', text: m[3] })
    last = m.index + m[0].length
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) })
  return out
}

/** 去掉行內標記，只留文字（表單標籤、提示用）。 */
export const plainText = (text: string) => parseInline(text).map(t => t.text).join('')

export interface ApiParam {
  name: string
  location: 'query' | 'path'
  required: boolean
  /** 文件寫的預設值；沒有預設（「—」）是 null。 */
  defaultValue: string | null
  description: string
}

export interface ApiEndpoint {
  method: string
  path: string
  /** 該節的第一段說明。 */
  summary: string
  scope: string | null
  params: ApiParam[]
}

const ENDPOINT_HEADING = /^(GET|POST|PUT|PATCH|DELETE) (\/external\/\S+)$/
const SCOPE_LINE = /需要權限：`([^`]+)`/

/** 從文件的 `### <METHOD> /external/...` 各節解析出端點、需要的權限與參數表。 */
export function endpointsOf(blocks: readonly Block[]): ApiEndpoint[] {
  const out: ApiEndpoint[] = []
  let current: ApiEndpoint | null = null
  for (const block of blocks) {
    if (block.kind === 'heading') {
      const m = block.level === 3 ? ENDPOINT_HEADING.exec(block.text) : null
      current = m ? { method: m[1], path: m[2], summary: '', scope: null, params: [] } : null
      if (current) out.push(current)
      continue
    }
    if (!current) continue
    if (block.kind === 'para') {
      const scope = SCOPE_LINE.exec(block.text)
      if (scope) current.scope = scope[1]
      else if (!current.summary) current.summary = block.text
    } else if (block.kind === 'table' && block.head[0] === '參數' && current.params.length === 0) {
      const col = (name: string) => block.head.indexOf(name)
      const [loc, req, def, desc] = [col('位置'), col('必填'), col('預設'), col('說明')]
      current.params = block.rows.map(row => {
        const defaultValue = def >= 0 ? plainText(row[def] ?? '') : ''
        return {
          name: plainText(row[0] ?? ''),
          location: row[loc] === 'path' ? 'path' : 'query',
          required: row[req] === '是',
          defaultValue: defaultValue && defaultValue !== '—' ? defaultValue : null,
          description: row[desc] ?? '',
        }
      })
    }
  }
  return out
}

/** 依表單值組出請求路徑：path 參數代入（缺值留 null 讓呼叫端擋），query 只帶有填的。 */
export function requestPath(endpoint: ApiEndpoint, values: Readonly<Record<string, string>>): string | null {
  let path = endpoint.path
  const query = new URLSearchParams()
  for (const p of endpoint.params) {
    const value = (values[p.name] ?? '').trim()
    if (p.location === 'path') {
      if (!value) return null
      path = path.replace(`{${p.name}}`, encodeURIComponent(value))
    } else if (value) {
      query.append(p.name, value)
    }
  }
  const qs = query.toString()
  return qs ? `${path}?${qs}` : path
}

/** 對應的 curl 指令：金鑰一律寫成 `$API_KEY`，畫面上與複製出去的指令都不含真金鑰。 */
export function curlCommand(method: string, url: string): string {
  const quoted = `'${url.replace(/'/g, `'\\''`)}'`
  const verb = method === 'GET' ? '' : ` -X ${method}`
  return `curl -sS${verb} -H "Authorization: Bearer $API_KEY" ${quoted}`
}
