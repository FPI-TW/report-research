import { describe, expect, test, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Fragment, createElement } from 'react'
import { inline } from './markdown'

const html = (nodes: ReturnType<typeof inline>) =>
  renderToStaticMarkup(createElement(Fragment, null, ...nodes))

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
