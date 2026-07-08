import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MonitorSkeleton } from './MonitorSkeleton'

describe('MonitorSkeleton', () => {
  it('渲染 aria-hidden 骨架容器', () => {
    render(<MonitorSkeleton />)
    expect(screen.getByTestId('monitor-skeleton').getAttribute('aria-hidden')).toBe('true')
  })
})
