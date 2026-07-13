import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, test, vi } from 'vitest'

vi.mock('../../lib/routePreload', () => ({ preloadRoute: vi.fn() }))
import { preloadRoute } from '../../lib/routePreload'
import { ModeSwitch } from './ModeSwitch'

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><ModeSwitch /></MemoryRouter>)
}

test('在 /search：檢索為 active、問答為連向 /ask 的連結', () => {
  renderAt('/search')
  expect(screen.getByText('檢索研報').closest('[aria-current="page"]')).not.toBeNull()
  expect(screen.getByRole('link', { name: /智能問答/ })).toHaveAttribute('href', '/ask')
})

test('在 /ask：問答為 active、檢索為連向 /search 的連結', () => {
  renderAt('/ask')
  expect(screen.getByText('智能問答').closest('[aria-current="page"]')).not.toBeNull()
  expect(screen.getByRole('link', { name: /檢索研報/ })).toHaveAttribute('href', '/search')
})

test('hover 非 active 連結預載對向路由', () => {
  renderAt('/search')
  fireEvent.pointerEnter(screen.getByRole('link', { name: /智能問答/ }))
  expect(preloadRoute).toHaveBeenCalledWith('ask')
})
