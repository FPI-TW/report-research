import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BriefCalendar } from './BriefCalendar'

const DATES = ['2026-10-05', '2026-10-03', '2026-09-30', '2026-08-12']

function setup(selected = '2026-10-03', today = '2026-10-28') {
  const onPick = vi.fn()
  const view = render(<BriefCalendar dates={DATES} selected={selected} today={today} onPick={onPick} />)
  return { onPick, ...view }
}

describe('BriefCalendar', () => {
  it('顯示選取日期所在的月份；有簡報的日期可點、沒有的不可點', () => {
    setup()
    expect(screen.getByRole('heading', { name: '2026 年 10 月' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '10 月 5 日' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '10 月 4 日，無簡報' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '10 月 3 日' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '10 月 5 日' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: '10 月 28 日，無簡報' })).toHaveAttribute('aria-current', 'date')
  })

  it('點有簡報的日期回報該日；點選取中的日期不重複觸發', () => {
    const { onPick } = setup()
    fireEvent.click(screen.getByRole('button', { name: '10 月 5 日' }))
    expect(onPick).toHaveBeenCalledWith('2026-10-05')
    fireEvent.click(screen.getByRole('button', { name: '10 月 3 日' }))
    expect(onPick).toHaveBeenCalledTimes(1)
  })

  it('上下月切換，範圍是最舊一份到今天所在的月份', () => {
    setup()
    const prev = screen.getByRole('button', { name: '上個月' })
    const next = screen.getByRole('button', { name: '下個月' })
    expect(next).toBeDisabled()
    fireEvent.click(prev)
    expect(screen.getByRole('heading', { name: '2026 年 9 月' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '9 月 30 日' })).toBeEnabled()
    fireEvent.click(prev)
    expect(screen.getByRole('heading', { name: '2026 年 8 月' })).toBeInTheDocument()
    expect(prev).toBeDisabled()
    expect(next).toBeEnabled()
  })

  it('選取的日期換到別的月份時，月曆跟著翻過去', () => {
    const { rerender, onPick } = setup()
    fireEvent.click(screen.getByRole('button', { name: '上個月' }))
    expect(screen.getByRole('heading', { name: '2026 年 9 月' })).toBeInTheDocument()
    rerender(<BriefCalendar dates={DATES} selected="2026-10-05" today="2026-10-28" onPick={onPick} />)
    expect(screen.getByRole('heading', { name: '2026 年 10 月' })).toBeInTheDocument()
  })
})
