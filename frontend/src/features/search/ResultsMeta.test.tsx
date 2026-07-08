import { it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ResultsMeta } from './ResultsMeta'

it('渲染 meta 文字', () => {
  render(<ResultsMeta text="全部研報 — 共 100 篇" />)
  expect(screen.getByText('全部研報 — 共 100 篇')).toBeTruthy()
})
