import { render } from '@testing-library/react'
import { Icon } from './Icon'

test('渲染指定 name 的 svg，套用 size 與 stroke', () => {
  const { container } = render(<Icon name="search" size={18} />)
  const svg = container.querySelector('svg')!
  expect(svg).toBeInTheDocument()
  expect(svg.getAttribute('width')).toBe('18')
  expect(svg.getAttribute('stroke-width')).toBe('1.8')
})
