import { describe, expect, test, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Fragment, createElement } from 'react'
import { inline, renderMarkdown } from './markdown'

const html = (nodes: ReturnType<typeof inline>) =>
  renderToStaticMarkup(createElement(Fragment, null, ...nodes))

const md = (s: string, maxCite = 0) =>
  renderToStaticMarkup(createElement(Fragment, null, ...renderMarkdown(s, maxCite)))

describe('inline', () => {
  test('純文字原樣（React 自動 escape）', () => {
    expect(html(inline('a < b & c', 0))).toBe('a &lt; b &amp; c')
  })
  test('行內碼不做樣式且內容跳脫', () => {
    expect(html(inline('用 `a<b` 比較', 0))).toBe('用 <code>a&lt;b</code> 比較')
  })
  test('粗體與斜體', () => {
    expect(html(inline('**粗** 與 *斜*', 0))).toBe('<strong>粗</strong> 與 <em>斜</em>')
  })
  test('http 連結 target/rel', () => {
    expect(html(inline('看 [連結](https://a.com/x) 吧', 0))).toBe(
      '看 <a href="https://a.com/x" target="_blank" rel="noopener">連結</a> 吧',
    )
  })
  test('[n] 在來源範圍內轉 chip，超出維持原樣', () => {
    expect(html(inline('見 [1] 與 [9]', 3))).toContain('class="cite"')
    expect(html(inline('見 [1] 與 [9]', 3))).toContain('[9]')
    expect(html(inline('見 [1] 與 [9]', 3))).not.toContain('data-n="9"')
  })
  test('maxCite=0 時 [1] 不轉 chip', () => {
    expect(html(inline('看 [1]', 0))).toBe('看 [1]')
  })
  test('cite chip 點擊呼叫 onCite(n)', () => {
    const onCite = vi.fn()
    const nodes = inline('見 [2]', 3, onCite)
    // 找出 chip 節點並觸發其 onClick
    const chip = nodes.find(
      (n): n is React.ReactElement<{ onClick?: () => void }> =>
        !!n && typeof n === 'object' && 'props' in n && (n as { props?: { className?: string } }).props?.className === 'cite',
    )
    chip?.props.onClick?.()
    expect(onCite).toHaveBeenCalledWith(2)
  })
})

describe('renderMarkdown', () => {
  test('h1~h6 夾到 h4', () => {
    expect(md('# 一')).toBe('<h1>一</h1>')
    expect(md('##### 五')).toBe('<h4>五</h4>')
  })
  test('黏行 ATX 標題前補換行（CJK 句末後）', () => {
    expect(md('收盤價。## 緯創')).toBe('<p>收盤價。</p><h2>緯創</h2>')
  })
  test('C# 與 #1 不被誤切/誤判標題', () => {
    expect(md('用 C# 開發')).toBe('<p>用 C# 開發</p>')
    expect(md('#1 名')).toBe('<p>#1 名</p>')
  })
  test('無序與有序清單', () => {
    expect(md('- a\n- b')).toBe('<ul><li>a</li><li>b</li></ul>')
    expect(md('1. a\n2. b')).toBe('<ol><li>a</li><li>b</li></ol>')
  })
  test('引用、分隔線、表格', () => {
    expect(md('> 引言')).toBe('<blockquote>引言</blockquote>')
    expect(md('---')).toBe('<hr/>')
    expect(md('| A | B |\n|---|---|\n| 1 | 2 |')).toContain('<table class="md-table">')
  })
  test('一般程式碼區塊跳脫', () => {
    expect(md('```\na<b\n```')).toBe('<pre><code>a&lt;b</code></pre>')
  })
  test('chart/kpi 圍欄顯示佔位（live 預覽）', () => {
    expect(md('```chart\n{"title":"營收"}\n```')).toBe('<p class="md-chart-ph">（圖表：營收）</p>')
    expect(md('```kpi\n{}\n```')).toBe('<p class="md-chart-ph">（重點數據）</p>')
  })
  test('段落內換行轉 <br>', () => {
    expect(md('一\n二')).toBe('<p>一<br/>二</p>')
  })
})
