import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { useAskController } from './useAskController'
import { setWebSearch } from './useWebSearch'
import * as api from './askApi'
import type { RawSSEEvent } from './readSSE'

// 網搜暫停（WEB_SEARCH_PAUSED，DeepSeek 遷移 PR-W）以可切換的 getter 模擬：預設走「恢復後」的行為，
// 讓開關本身的測試在暫停期間繼續守著接回點；暫停中的行為另成一組，把旗標設成 true。
const paused = vi.hoisted(() => ({ value: false }))
vi.mock('./useWebSearch', async (importOriginal) => {
  const mod = await importOriginal<typeof import('./useWebSearch')>()
  return { ...mod, get WEB_SEARCH_PAUSED() { return paused.value } }
})

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return createElement(QueryClientProvider, { client: qc }, children)
}

async function* askDone(): AsyncGenerator<RawSSEEvent> {
  yield { event: 'done', data: { qa_id: 'qa1', conversation_id: 'c1' } }
}

// 開關是 module 級 store，而請求體在 runStream 內組出來——漏掉 deps 陣列裡的 web
// 會讓「切換後第一題仍用舊值」，症狀是使用者開了網搜卻拿到沒查網路的答案。
describe('useAskController web-search threading (M11)', () => {
  beforeEach(() => { vi.restoreAllMocks(); localStorage.clear(); setWebSearch(false); paused.value = false })
  afterEach(() => { setWebSearch(false); localStorage.clear() })

  it('預設把 web=false 帶進 /api/ask', async () => {
    const askSpy = vi.spyOn(api, 'streamAsk').mockReturnValue(askDone())
    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.submit('台積電') })
    await waitFor(() => expect(askSpy).toHaveBeenCalled())
    expect(askSpy.mock.calls[0][0]).toMatchObject({ web: false })
  })

  it('開啟後 /api/ask 帶 web=true', async () => {
    const askSpy = vi.spyOn(api, 'streamAsk').mockReturnValue(askDone())
    setWebSearch(true)
    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.submit('台積電最新消息') })
    await waitFor(() => expect(askSpy).toHaveBeenCalled())
    expect(askSpy.mock.calls[0][0]).toMatchObject({ web: true })
  })

  it('渲染後才切換也要生效（runStream 需把 web 收進 deps）', async () => {
    const askSpy = vi.spyOn(api, 'streamAsk').mockReturnValue(askDone())
    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { setWebSearch(true) })
    act(() => { result.current.submit('台積電最新消息') })
    await waitFor(() => expect(askSpy).toHaveBeenCalled())
    expect(askSpy.mock.calls[0][0]).toMatchObject({ web: true })
  })

  it('重新生成也沿用當下的開關', async () => {
    const askSpy = vi.spyOn(api, 'streamAsk').mockReturnValue(askDone())
    setWebSearch(true)
    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.regenerate('t1', 'qa1', '台積電') })
    await waitFor(() => expect(askSpy).toHaveBeenCalled())
    expect(askSpy.mock.calls[0][0]).toMatchObject({ web: true })
  })
})

// 網搜暫停中（PR-W）：localStorage 殘留的 web=true 不送出。送了會被伺服器總閘 ASK_ENABLE_WEB=0 擋掉，
// 但 qa_log.filters.web 會記到一個使用者在畫面上看不到的開關狀態。
describe('網搜暫停中（WEB_SEARCH_PAUSED）', () => {
  beforeEach(() => { vi.restoreAllMocks(); localStorage.clear(); setWebSearch(false); paused.value = true })
  afterEach(() => { setWebSearch(false); localStorage.clear(); paused.value = false })

  it('殘留的開啟偏好仍送 web=false', async () => {
    const askSpy = vi.spyOn(api, 'streamAsk').mockReturnValue(askDone())
    setWebSearch(true)
    const { result } = renderHook(() => useAskController(), { wrapper })
    act(() => { result.current.submit('台積電最新消息') })
    await waitFor(() => expect(askSpy).toHaveBeenCalled())
    expect(askSpy.mock.calls[0][0]).toMatchObject({ web: false })
  })
})
