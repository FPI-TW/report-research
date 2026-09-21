import { render, screen } from '@testing-library/react'
import { createMemoryRouter, Outlet, RouterProvider } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { routes } from '../../App'
import { RouteError } from './RouteError'

// 邊界會 console.error（刻意的可觀測痕跡），React 自己也會印一次；測試輸出不需要它們。
beforeEach(() => { vi.spyOn(console, 'error').mockImplementation(() => {}) })
afterEach(() => vi.restoreAllMocks())

function Boom({ message }: { message: string }): never {
  throw new Error(message)
}

function Shell() {
  return (
    <div>
      <nav>側欄</nav>
      <Outlet />
    </div>
  )
}

/** 與 App.tsx 同構：外殼一層、內容區一層。 */
function renderWith(message: string) {
  const router = createMemoryRouter(
    [{
      element: <Shell />,
      errorElement: <RouteError standalone />,
      children: [{
        errorElement: <RouteError />,
        children: [{ path: '/', element: <Boom message={message} /> }],
      }],
    }],
    { initialEntries: ['/'] },
  )
  render(<RouterProvider router={router} />)
}

test('頁面 render 丟例外：內容區換成錯誤畫面，外殼仍在', () => {
  renderWith('Cannot read properties of undefined')
  expect(screen.getByRole('alert')).toHaveTextContent('這一頁發生問題')
  expect(screen.getByText('Cannot read properties of undefined')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '重新載入' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '回到檢索' })).toHaveAttribute('href', '/search')
  // 這是掛兩層的理由：只有外層的話，側欄會跟著一起消失。
  expect(screen.getByText('側欄')).toBeInTheDocument()
})

test('部署後舊分頁抓不到 chunk：說明是版本更新而不是壞掉，只給重新載入', () => {
  renderWith('Failed to fetch dynamically imported module: https://x/app/assets/RadarPage-abc.js')
  expect(screen.getByRole('alert')).toHaveTextContent('站台已更新')
  expect(screen.getByRole('button', { name: '重新載入' })).toBeInTheDocument()
  // 切去別頁一樣會撞同一個問題（別頁的 chunk 檔名也換了），所以不給那條出口。
  expect(screen.queryByRole('link', { name: '回到檢索' })).not.toBeInTheDocument()
})

test('App 的路由表兩層都掛了錯誤邊界', () => {
  const [shell] = routes
  expect(shell.errorElement).toBeTruthy()
  expect(shell.children).toHaveLength(1)
  expect(shell.children[0].errorElement).toBeTruthy()
  expect(shell.children[0].children.some((r) => r.path === '/search')).toBe(true)
})
