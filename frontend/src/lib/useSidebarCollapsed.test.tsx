import { act, renderHook } from '@testing-library/react'
import { beforeEach, expect, test } from 'vitest'
import { useSidebarCollapsed } from './useSidebarCollapsed'

beforeEach(() => localStorage.clear())

test('預設展開；toggle 後收合並寫入 localStorage', () => {
  const { result } = renderHook(() => useSidebarCollapsed())
  expect(result.current.collapsed).toBe(false)
  act(() => result.current.toggle())
  expect(result.current.collapsed).toBe(true)
  expect(localStorage.getItem('tf.sidebar.collapsed')).toBe('1')
})
