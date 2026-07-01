import { test, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { ViewSwitch } from './ViewSwitch'

const wrap = (ui: React.ReactElement) => render(<MantineProvider>{ui}</MantineProvider>)

test('目前檢視 aria-checked=true', () => {
  wrap(<ViewSwitch value="group" onChange={vi.fn()} />)
  expect(screen.getByRole('radio', { name: '列表' })).toHaveAttribute('aria-checked', 'true')
  expect(screen.getByRole('radio', { name: '表格' })).toHaveAttribute('aria-checked', 'false')
})

test('點表格觸發 onChange("table")', () => {
  const onChange = vi.fn()
  wrap(<ViewSwitch value="group" onChange={onChange} />)
  fireEvent.click(screen.getByRole('radio', { name: '表格' }))
  expect(onChange).toHaveBeenCalledWith('table')
})

test('按鈕皆為 type="button"（避免誤觸表單送出）', () => {
  wrap(<ViewSwitch value="group" onChange={vi.fn()} />)
  expect(screen.getByRole('radio', { name: '列表' })).toHaveAttribute('type', 'button')
  expect(screen.getByRole('radio', { name: '表格' })).toHaveAttribute('type', 'button')
})
