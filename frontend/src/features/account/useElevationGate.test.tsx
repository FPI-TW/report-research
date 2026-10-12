import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { expect, test, vi } from 'vitest'
import { ApiError } from '../../lib/api'
import { ElevationCancelledError, useElevationGate, type ElevateFn } from './useElevationGate'

function Harness({ action, elevate, needTotp = false }: {
  action: () => Promise<string>; elevate: ElevateFn; needTotp?: boolean
}) {
  const { guard, dialog } = useElevationGate(elevate, needTotp)
  const [result, setResult] = useState('')
  return (
    <>
      <button type="button" onClick={() => {
        guard(action).then(setResult, (e: unknown) =>
          setResult(e instanceof ElevationCancelledError ? 'cancelled' : `error:${(e as Error).message}`))
      }}>do</button>
      <output>{result}</output>
      {dialog}
    </>
  )
}

const needsElevation = () => new ApiError(403, '這項操作需要重新驗證密碼', 'elevation_required')

test('收到 elevation_required 才彈出驗證框，驗證成功後自動重試同一個動作', async () => {
  let elevated = false
  const action = vi.fn(async () => { if (!elevated) throw needsElevation(); return 'done' })
  const elevate = vi.fn(async () => { elevated = true; return {} })
  render(<Harness action={action} elevate={elevate} />)
  fireEvent.click(screen.getByRole('button', { name: 'do' }))
  const dialog = await screen.findByRole('dialog', { name: '重新驗證身分' })
  expect(screen.queryByLabelText('驗證碼')).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('密碼'), { target: { value: 'fixed-test-secret-elevate1' } })
  fireEvent.click(screen.getByRole('button', { name: '驗證' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('done'))
  expect(elevate).toHaveBeenCalledWith({ password: 'fixed-test-secret-elevate1', code: undefined })
  expect(action).toHaveBeenCalledTimes(2)
  await waitFor(() => expect(dialog).not.toBeInTheDocument())
})

test('不需要提升時直接執行，不出現對話框；其他錯誤原樣拋出', async () => {
  const action = vi.fn(async () => { throw new ApiError(409, '至少要保留一位啟用中的管理員', 'last_admin') })
  render(<Harness action={action} elevate={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'do' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('error:至少要保留一位啟用中的管理員'))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('後端回 totp_required 時補問驗證碼；取消則 reject ElevationCancelledError', async () => {
  const action = vi.fn(async () => { throw needsElevation() })
  const elevate = vi.fn(async (body: { password: string; code?: string | null }) => {
    if (!body.code) throw new ApiError(403, '請輸入驗證碼', 'totp_required')
    throw new ApiError(403, '密碼或驗證碼不正確', 'bad_password')
  })
  render(<Harness action={action} elevate={elevate} />)
  fireEvent.click(screen.getByRole('button', { name: 'do' }))
  await screen.findByRole('dialog')
  fireEvent.change(screen.getByLabelText('密碼'), { target: { value: 'fixed-test-secret-elevate1' } })
  fireEvent.click(screen.getByRole('button', { name: '驗證' }))
  const code = await screen.findByLabelText('驗證碼')
  fireEvent.change(code, { target: { value: '123 456' } })
  fireEvent.click(screen.getByRole('button', { name: '驗證' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('密碼或驗證碼不正確')
  expect(elevate).toHaveBeenLastCalledWith({ password: 'fixed-test-secret-elevate1', code: '123 456' })
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('cancelled'))
})

test('帳號開了 TOTP（needTotp）時驗證碼欄位一開始就在', async () => {
  render(<Harness action={async () => { throw needsElevation() }} elevate={vi.fn()} needTotp />)
  fireEvent.click(screen.getByRole('button', { name: 'do' }))
  expect(await screen.findByLabelText('驗證碼')).toBeInTheDocument()
})
