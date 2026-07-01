import { test, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { MantineProvider } from '@mantine/core'
import { AppNav } from './AppNav'

const wrap = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]} basename="/app">
      <MantineProvider>
        <AppNav />
      </MantineProvider>
    </MemoryRouter>,
  )

test('渲染品牌與三個導覽連結（正確 href）', () => {
  wrap('/app/search')
  expect(screen.getByText('廷豐智能研報')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '搜尋' })).toHaveAttribute('href', '/app/search')
  expect(screen.getByRole('link', { name: '問答' })).toHaveAttribute('href', '/app/ask')
  expect(screen.getByRole('link', { name: '監控' })).toHaveAttribute('href', '/app/monitor')
})

test('主導覽為具名 landmark', () => {
  wrap('/app/search')
  expect(screen.getByRole('navigation', { name: '主導覽' })).toBeInTheDocument()
})

test('目前分路的連結帶 aria-current=page，其餘無', () => {
  wrap('/app/ask')
  expect(screen.getByRole('link', { name: '問答' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: '搜尋' })).not.toHaveAttribute('aria-current', 'page')
})
