import { test, expect } from 'vitest'
import { render } from '@testing-library/react'
import { Highlight } from './Highlight'

test('命中段渲染為 <mark>', () => {
  const { container } = render(<Highlight text="AI server" terms={['AI']} />)
  const marks = container.querySelectorAll('mark')
  expect(marks).toHaveLength(1)
  expect(marks[0].textContent).toBe('AI')
})

test('無 terms 不產生 mark', () => {
  const { container } = render(<Highlight text="hello" terms={[]} />)
  expect(container.querySelectorAll('mark')).toHaveLength(0)
  expect(container.textContent).toBe('hello')
})

test('XSS：注入字串以純文字呈現、不產生 script', () => {
  const { container } = render(
    <Highlight text="<script>alert(1)</script>x" terms={['x']} />,
  )
  expect(container.querySelector('script')).toBeNull()
  expect(container.textContent).toContain('<script>alert(1)</script>')
})
