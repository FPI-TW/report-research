import { describe, expect, it } from 'vitest'
import { curlCommand, endpointsOf, parseInline, parseMarkdown, requestPath, withBaseUrl, type ApiEndpoint } from './apiDoc'
import { EXTERNAL_API_MD } from './externalApiDoc'

describe('parseMarkdown', () => {
  it('標題、段落、清單、圍欄程式碼與表格', () => {
    const blocks = parseMarkdown([
      '# 標題', '', '第一行', '第二行', '', '- 甲', '  接續', '- 乙', '', '1. 一', '2. 二', '',
      '```bash', 'curl -sS x', '', '# 不是標題', '```', '', '| 參數 | 說明 |', '|---|---|', '| `q` | 查詢 |',
    ].join('\n'))
    expect(blocks).toEqual([
      { kind: 'heading', level: 1, text: '標題' },
      { kind: 'para', text: '第一行 第二行' },
      { kind: 'list', ordered: false, items: ['甲 接續', '乙'] },
      { kind: 'list', ordered: true, items: ['一', '二'] },
      { kind: 'code', lang: 'bash', text: 'curl -sS x\n\n# 不是標題' },
      { kind: 'table', head: ['參數', '說明'], rows: [['`q`', '查詢']] },
    ])
  })

  it('行內：code、粗體、http(s) 連結；其他 scheme 的連結只留文字', () => {
    expect(parseInline('用 `q` 與 **粗體**，見 [說明](https://example.com) 與 [壞](javascript:alert(1))')).toEqual([
      { kind: 'text', text: '用 ' },
      { kind: 'code', text: 'q' },
      { kind: 'text', text: ' 與 ' },
      { kind: 'strong', text: '粗體' },
      { kind: 'text', text: '，見 ' },
      { kind: 'link', text: '說明', href: 'https://example.com' },
      { kind: 'text', text: ' 與 ' },
      { kind: 'text', text: '壞' },
      { kind: 'text', text: ')' },
    ])
  })
})

describe('docs/EXTERNAL_API.md', () => {
  const endpoints = endpointsOf(parseMarkdown(EXTERNAL_API_MD))
  const byPath = (path: string) => endpoints.find(e => e.path === path)!

  it('解析得出每個端點的權限與參數表（試打表單的來源）', () => {
    expect(endpoints.length).toBeGreaterThan(0)
    for (const ep of endpoints) {
      expect(ep.method).toBe('GET')
      expect(ep.scope).toBeTruthy()
      expect(ep.summary).toBeTruthy()
      // 路徑裡的每個 {參數} 都要在參數表裡、而且標成 path
      for (const [, name] of ep.path.matchAll(/\{(\w+)\}/g)) {
        expect(ep.params.find(p => p.name === name)).toMatchObject({ location: 'path', required: true })
      }
    }
    const search = byPath('/external/v1/search')
    expect(search.scope).toBe('search')
    expect(search.params.find(p => p.name === 'q')).toMatchObject({ location: 'query', required: true, defaultValue: null })
    expect(search.params.find(p => p.name === 'limit')).toMatchObject({ required: false, defaultValue: '10' })
  })

  it('站台網址只寫佔位字，交出去前代入；整份文件只用渲染器支援的語法', () => {
    expect(EXTERNAL_API_MD).toContain('{BASE_URL}')
    const md = withBaseUrl(EXTERNAL_API_MD, 'https://research.example.com')
    expect(md).not.toContain('{BASE_URL}')
    expect(md).toContain('https://research.example.com/external/v1/search?')
    // 不支援的區塊（引言、分隔線、HTML）會被當成一般段落原樣顯示：文件裡不該出現
    for (const block of parseMarkdown(md)) {
      if (block.kind === 'para') expect(block.text).not.toMatch(/^(>|---|<)/)
    }
  })
})

describe('requestPath / curlCommand', () => {
  const ep: ApiEndpoint = {
    method: 'GET', path: '/external/v1/reports/{report_id}/file-url', summary: '', scope: 'report.file',
    params: [
      { name: 'report_id', location: 'path', required: true, defaultValue: null, description: '' },
      { name: 'q', location: 'query', required: false, defaultValue: null, description: '' },
    ],
  }

  it('path 參數代入並編碼；query 只帶有填的；缺 path 參數回 null', () => {
    expect(requestPath(ep, { report_id: ' a/b ', q: '' })).toBe('/external/v1/reports/a%2Fb/file-url')
    expect(requestPath(ep, { report_id: 'x', q: 'AI 伺服器' })).toBe('/external/v1/reports/x/file-url?q=AI+%E4%BC%BA%E6%9C%8D%E5%99%A8')
    expect(requestPath(ep, { q: 'x' })).toBeNull()
  })

  it('curl 指令用 $API_KEY、網址單引號包起來', () => {
    expect(curlCommand('GET', "https://h/x?q=it's")).toBe(
      `curl -sS -H "Authorization: Bearer $API_KEY" 'https://h/x?q=it'\\''s'`,
    )
  })
})
