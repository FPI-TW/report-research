import { act, renderHook } from '@testing-library/react'
import { beforeEach, expect, test } from 'vitest'
import { useSidebarCollapsed } from './useSidebarCollapsed'

beforeEach(() => localStorage.clear())

test('預設展開；toggle 後收合並寫入 localStorage', () => {
  const { result } = renderHook(() => useSidebarCollapsed())
  expect(result.current.collapsed).toBe(false)
  act(() => result.current.toggle())
  expect(result.current.collapsed).toBe(true)
  expect(localStorage.getItem('tf.sidebar.v2.collapsed')).toBe('1')
})

test('記住使用者收合過的選擇', () => {
  localStorage.setItem('tf.sidebar.v2.collapsed', '1')
  const { result } = renderHook(() => useSidebarCollapsed())
  expect(result.current.collapsed).toBe(true)
})

test('舊版側欄的收合紀錄不沿用（新版預設展開）', () => {
  localStorage.setItem('tf.sidebar.collapsed', '1')
  const { result } = renderHook(() => useSidebarCollapsed())
  expect(result.current.collapsed).toBe(false)
})
