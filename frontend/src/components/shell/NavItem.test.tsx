import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { NavItem } from './NavItem'

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <NavItem to="/search" icon="search" label="檢索" variant="row" />
    </MemoryRouter>,
  )
}

test('在對應路徑時標記 aria-current=page', () => {
  renderAt('/search')
  expect(screen.getByRole('link', { name: /檢索/ })).toHaveAttribute('aria-current', 'page')
})

test('不在對應路徑時無 aria-current', () => {
  renderAt('/ask')
  expect(screen.getByRole('link', { name: /檢索/ })).not.toHaveAttribute('aria-current')
})
