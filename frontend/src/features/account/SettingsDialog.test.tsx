import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import type { SettingsTab } from '../../components/shell/shellContext'
import { setLocale } from '../../lib/useLocale'
import { SettingsDialog } from './SettingsDialog'

beforeEach(() => { localStorage.clear(); setLocale('zh-Hant') })
afterEach(() => { vi.unstubAllGlobals(); setLocale('zh-Hant') })

function Harness({ initial = 'account' as SettingsTab, onClose = () => {} }) {
  const [tab, setTab] = useState<SettingsTab>(initial)
  return <SettingsDialog open tab={tab} onTab={setTab} onClose={onClose} />
}

function wrap(initial: SettingsTab = 'account', status = { status: 'ok', message: '系統運作正常' }) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/me') return new Response(JSON.stringify({ id: 'u1', username: 'analyst', role: 'user', totp_enabled: false }), { status: 200 })
    if (url === '/api/status') return new Response(JSON.stringify(status), { status: 200 })
    if (url === '/api/me/totp') return new Response(JSON.stringify({ enabled: false }), { status: 200 })
    return new Response(JSON.stringify({ total_reports: 0, total_chunks: 0, markets: [], instrument_types: [], report_types: [], username: 'analyst' }), { status: 200 })
  }))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><MemoryRouter><Harness initial={initial} /></MemoryRouter></QueryClientProvider>)
}

test('四個分頁：帳號、安全、偏好、關於；預設帳號', async () => {
  wrap()
  const tabs = screen.getAllByRole('tab').map(t => t.textContent)
  expect(tabs).toEqual(['帳號', '安全', '偏好', '關於'])
  expect(screen.getByRole('tab', { name: '帳號' })).toHaveAttribute('aria-selected', 'true')
  expect(await screen.findAllByText('analyst')).not.toHaveLength(0)
})

test('↑↓ 切換分頁（垂直 tablist）', () => {
  wrap()
  const account = screen.getByRole('tab', { name: '帳號' })
  fireEvent.keyDown(account, { key: 'ArrowDown' })
  expect(screen.getByRole('tab', { name: '安全' })).toHaveAttribute('aria-selected', 'true')
  fireEvent.keyDown(account, { key: 'End' })
  expect(screen.getByRole('tab', { name: '關於' })).toHaveAttribute('aria-selected', 'true')
})

test('安全分頁就是兩步驟驗證設定', async () => {
  wrap('security')
  expect(await screen.findByText('兩步驟驗證')).toBeInTheDocument()
})

test('偏好：回答語言預設中文，切到 English 會記住', () => {
  wrap('prefs')
  const en = screen.getByRole('radio', { name: 'English' })
  expect(screen.getByRole('radio', { name: '中文' })).toHaveAttribute('aria-checked', 'true')
  fireEvent.click(en)
  expect(en).toHaveAttribute('aria-checked', 'true')
  expect(localStorage.getItem('tf.locale')).toBe('en')
})

test('關於：系統狀態與使用說明', async () => {
  wrap('about', { status: 'degraded', message: '部分核心服務異常' })
  const row = await screen.findByRole('status', { name: '系統狀態：部分異常' })
  expect(row).toHaveTextContent('系統狀態')
  expect(row).toHaveTextContent('部分異常')
  expect(screen.getByText('部分核心服務異常')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /使用說明/ })).toBeInTheDocument()
})

test('關於：正常時只寫「正常」，不多一行重述的說明', async () => {
  wrap('about', { status: 'ok', message: '系統運作正常' })
  const row = await screen.findByRole('status', { name: '系統狀態：正常' })
  expect(row).toHaveTextContent('正常')
  expect(screen.queryByText('系統運作正常')).not.toBeInTheDocument()
  // 不再是「系統狀態：正常」一整句
  expect(screen.queryByText('系統狀態：正常')).not.toBeInTheDocument()
})
