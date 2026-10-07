import { act, renderHook } from '@testing-library/react'
import { beforeEach, expect, test } from 'vitest'
import { useSidebarCollapsed } from './useSidebarCollapsed'

beforeEach(() => localStorage.clear())

test('預設收合；toggle 後展開並寫入 localStorage', () => {
  const { result } = renderHook(() => useSidebarCollapsed())
  expect(result.current.collapsed).toBe(true)
  act(() => result.current.toggle())
  expect(result.current.collapsed).toBe(false)
  expect(localStorage.getItem('tf.sidebar.collapsed')).toBe('0')
})

test('記住使用者展開過的選擇', () => {
  localStorage.setItem('tf.sidebar.collapsed', '0')
  const { result } = renderHook(() => useSidebarCollapsed())
  expect(result.current.collapsed).toBe(false)
})
