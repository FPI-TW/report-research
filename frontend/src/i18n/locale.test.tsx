import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MobileTabBar } from '../components/shell/MobileTabBar'
import { fromResponse, setBranding, useBranding } from '../lib/branding'
import { LocaleBoundary, getLocale, registerMessages, setLocale, t, tx, useLocale } from '.'
import zhApp from './messages/zh-Hant/app'

afterEach(() => {
  setBranding(null)
  vi.resetModules()
})

describe('t()', () => {
  it('依目前語系查字典並內插變數', () => {
    expect(t('admin.adminReportsPage.selected', { length: 3 })).toBe('已選 3 篇')
    act(() => setLocale('en'))
    expect(t('admin.adminReportsPage.selected', { length: 3 })).toBe('3 selected')
  })

  it('英文的單複數依 count（或第一個數字變數）挑', () => {
    act(() => setLocale('en'))
    expect(t('search.resultsSection.reports', { total: 1 })).toBe('1 report')
    expect(t('search.resultsSection.reports', { total: 12 })).toBe('12 reports')
  })

  it('tx() 把 <0>…</0> 換成元素，語序照譯文', () => {
    registerMessages('zh-Hant', 'app', { ...zhApp, 'test.rich': '共 <0>{n}</0> 篇' })
    try {
      render(<p data-testid="p">{tx('test.rich' as never, [c => <strong>{c}</strong>], { n: 5 })}</p>)
      expect(screen.getByTestId('p').innerHTML).toBe('共 <strong>5</strong> 篇')
    } finally {
      registerMessages('zh-Hant', 'app', zhApp)
    }
  })
})

function Wrap({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}><LocaleBoundary><MemoryRouter>{children}</MemoryRouter></LocaleBoundary></QueryClientProvider>
}

describe('切換語系', () => {
  it('畫面換成英文，html lang、localStorage 與 cookie 一起更新', () => {
    render(<Wrap><MobileTabBar /></Wrap>)
    expect(screen.getByText('研報庫')).toBeInTheDocument()
    act(() => setLocale('en'))
    expect(screen.getByText('Library')).toBeInTheDocument()
    expect(screen.queryByText('研報庫')).not.toBeInTheDocument()
    expect(document.documentElement.lang).toBe('en')
    expect(localStorage.getItem('tf.locale')).toBe('en')
    expect(document.cookie).toContain('tf_locale=en')
  })

  it('切回中文', () => {
    render(<Wrap><MobileTabBar /></Wrap>)
    act(() => setLocale('en'))
    act(() => setLocale('zh-Hant'))
    expect(screen.getByText('研報庫')).toBeInTheDocument()
    expect(getLocale()).toBe('zh-Hant')
  })

  it('問答輸出語言與介面語系是同一個偏好（useLocale 讀同一個 store）', () => {
    function Probe() { return <span data-testid="loc">{useLocale()}</span> }
    render(<Probe />)
    act(() => setLocale('en'))
    expect(screen.getByTestId('loc')).toHaveTextContent('en')
  })
})

describe('初始語系：使用者偏好 → profile 預設 → zh-Hant', () => {
  async function fresh() {
    vi.resetModules()
    return import('.')
  }

  it('沒有偏好：用 profile 預設', async () => {
    const mod = await fresh()
    await mod.initI18n(Promise.resolve({ defaultLocale: 'en', availableLocales: ['zh-Hant', 'en'] }))
    expect(mod.getLocale()).toBe('en')
    expect(mod.t('shell.sidebar.library')).toBe('Library')
  })

  it('沒有偏好也沒有 profile（品牌抓不到）：zh-Hant', async () => {
    const mod = await fresh()
    await mod.initI18n(Promise.resolve(null))
    expect(mod.getLocale()).toBe('zh-Hant')
  })

  it('沿用舊的 tf.locale 偏好（M10b 的問答輸出語言），不等 profile', async () => {
    localStorage.setItem('tf.locale', 'en')
    const mod = await fresh()
    await mod.initI18n(new Promise(() => {}))
    expect(mod.getLocale()).toBe('en')
  })

  it('只有 cookie（登入頁選的語言）也算使用者偏好', async () => {
    document.cookie = 'tf_locale=en; path=/'
    const mod = await fresh()
    await mod.initI18n(Promise.resolve({ defaultLocale: 'zh-Hant' }))
    expect(mod.getLocale()).toBe('en')
  })

  it('偏好不在 profile 開放的語系裡：改用 profile 預設', async () => {
    localStorage.setItem('tf.locale', 'en')
    const mod = await fresh()
    await mod.initI18n(Promise.resolve({ defaultLocale: 'zh-Hant', availableLocales: ['zh-Hant'] }))
    await vi.waitFor(() => expect(mod.getLocale()).toBe('zh-Hant'))
    expect(mod.availableLocales()).toEqual(['zh-Hant'])
  })

  it('不認得的偏好值當作沒選過', async () => {
    localStorage.setItem('tf.locale', 'fr')
    const mod = await fresh()
    await mod.initI18n(Promise.resolve({ defaultLocale: 'en' }))
    expect(mod.getLocale()).toBe('en')
  })
})

describe('品牌名稱依語系', () => {
  function Name() { return <span data-testid="name">{useBranding().productName}</span> }

  it('英文介面用 *_en，沒給就用原名', () => {
    setBranding(fromResponse({ institution_name: '艾克米', product_name: '艾克米研究平台', product_name_en: 'Acme Research' }))
    render(<LocaleBoundary><Name /></LocaleBoundary>)
    expect(screen.getByTestId('name')).toHaveTextContent('艾克米研究平台')
    act(() => setLocale('en'))
    expect(screen.getByTestId('name')).toHaveTextContent('Acme Research')
    act(() => setBranding(fromResponse({ institution_name: '艾克米', product_name: '艾克米研究平台' })))
    expect(screen.getByTestId('name')).toHaveTextContent('艾克米研究平台')
  })

  it('profile 沒給名稱：中性預設跟著語系', () => {
    render(<LocaleBoundary><Name /></LocaleBoundary>)
    expect(screen.getByTestId('name')).toHaveTextContent('智能研報')
    act(() => setLocale('en'))
    expect(screen.getByTestId('name')).toHaveTextContent('Research Intelligence')
  })

  it('profile 的 [locale] 經 /api/branding 帶給前端；不認得的語系丟掉', () => {
    const b = fromResponse({ locale: { default: 'en', available: ['zh-Hant', 'en', 'fr'] } })
    expect(b.defaultLocale).toBe('en')
    expect(b.availableLocales).toEqual(['zh-Hant', 'en'])
    expect(fromResponse({}).defaultLocale).toBeNull()
  })
})
