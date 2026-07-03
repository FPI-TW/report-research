import { useRef } from 'react'
import { fireEvent, render } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { useClickOutside } from './useClickOutside'

function Harness({ onOutside }: { onOutside: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useClickOutside(ref, onOutside)
  return (
    <div>
      <div ref={ref} data-testid="inside">inside</div>
      <button data-testid="outside">outside</button>
    </div>
  )
}

test('點 ref 外觸發、點內不觸發', () => {
  const cb = vi.fn()
  const { getByTestId } = render(<Harness onOutside={cb} />)
  fireEvent.mouseDown(getByTestId('inside'))
  expect(cb).not.toHaveBeenCalled()
  fireEvent.mouseDown(getByTestId('outside'))
  expect(cb).toHaveBeenCalledTimes(1)
})
