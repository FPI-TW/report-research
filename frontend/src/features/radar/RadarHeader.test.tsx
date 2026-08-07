import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { RadarHeader } from './RadarHeader'

let notifyIntersection: IntersectionObserverCallback | undefined

beforeEach(() => {
  notifyIntersection = undefined
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback: IntersectionObserverCallback) {
      notifyIntersection = callback
    }

    observe() {}
    disconnect() {}
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

test('頁首在吸頂後補上目前標的，返回與窗期控制維持可操作', () => {
  const onBack = vi.fn()
  const onWindowChange = vi.fn()

  render(
    <RadarHeader
      market="TW"
      marketDisplay="台股"
      code="8046"
      name="南電"
      asOf="2026-07-11"
      coverage={{
        state: 'ok',
        brokers_total: 12,
        brokers_extracted: 12,
        brokers_in_consensus: 10,
        reports_available: 91,
        note: '',
      }}
      window="90"
      onWindowChange={onWindowChange}
      onBack={onBack}
    />,
  )

  const breadcrumb = screen.getByRole('navigation', { name: '麵包屑' })
  expect(breadcrumb).toHaveTextContent('券商觀點/台股')
  expect(screen.getAllByText('南電')).toHaveLength(1)
  expect(screen.queryByText('南電', { selector: '[aria-current="page"]' })).not.toBeInTheDocument()

  act(() => {
    notifyIntersection?.(
      [{ isIntersecting: false } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    )
  })

  expect(screen.getByText('南電', { selector: '[aria-current="page"]' })).toBeInTheDocument()
  expect(breadcrumb).toHaveTextContent('南電8046')

  fireEvent.click(screen.getByRole('button', { name: '券商觀點' }))
  fireEvent.click(screen.getByRole('radio', { name: '30 天' }))
  expect(onBack).toHaveBeenCalledOnce()
  expect(onWindowChange).toHaveBeenCalledWith('30')
})
