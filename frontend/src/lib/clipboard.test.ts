import { afterEach, describe, expect, it, vi } from 'vitest'
import { copyText } from './clipboard'

/**
 * 這支測試存在的理由：`execCommand` 後備是這個模組的**全部意義**——本站的區網入口是
 * `http://192.168.1.128:8097/`（HTTP ＋ 私有 IP ＝ 非安全情境），那裡
 * `navigator.clipboard` 是 undefined。而同事幾乎都走那個網址。
 * 沒有測試的話，這條路徑上線後永遠不會被執行到，直到有人抱怨貼不出來。
 */
function setSecureContext(v: boolean) {
  Object.defineProperty(window, 'isSecureContext', { value: v, configurable: true })
}

function setClipboard(v: unknown) {
  Object.defineProperty(navigator, 'clipboard', { value: v, configurable: true })
}

/**
 * **jsdom 沒有 document.execCommand**（連屬性都不存在，所以 vi.spyOn 會直接拋）。
 * 這件事本身就是那條後備從未被任何測試執行過的原因——得自己掛上去才驗得到。
 */
function setExecCommand(impl: () => boolean): ReturnType<typeof vi.fn> {
  const fn = vi.fn(impl)
  Object.defineProperty(document, 'execCommand', { value: fn, configurable: true, writable: true })
  return fn
}

afterEach(() => {
  vi.restoreAllMocks()
  setSecureContext(true)
  setClipboard(undefined)
})

describe('copyText', () => {
  it('安全情境 → 直接用 navigator.clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setSecureContext(true)
    setClipboard({ writeText })

    await copyText('台積電先進封裝')
    expect(writeText).toHaveBeenCalledWith('台積電先進封裝')
  })

  // 非安全情境下 navigator.clipboard 根本不存在（不是被拒絕）
  it('非安全情境 → 走 execCommand 後備，且不碰 navigator.clipboard', async () => {
    const writeText = vi.fn()
    setSecureContext(false)
    setClipboard({ writeText })
    const exec = setExecCommand(() => true)

    await expect(copyText('券商原句')).resolves.toBeUndefined()
    expect(exec).toHaveBeenCalledWith('copy')
    expect(writeText).not.toHaveBeenCalled()
  })

  it('clipboard API 不存在時同樣落到後備', async () => {
    setSecureContext(true)
    setClipboard(undefined)
    const exec = setExecCommand(() => true)

    await expect(copyText('券商原句')).resolves.toBeUndefined()
    expect(exec).toHaveBeenCalled()
  })

  // 失敗一定要 reject，呼叫端才有機會說「請手動複製」——吞掉等於再造一次同樣的坑
  it('後備也失敗 → reject（不可以假裝成功）', async () => {
    setSecureContext(false)
    setClipboard(undefined)
    setExecCommand(() => false)

    await expect(copyText('券商原句')).rejects.toThrow()
  })

  it('execCommand 拋例外時也 reject，不外洩例外型別', async () => {
    setSecureContext(false)
    setClipboard(undefined)
    setExecCommand(() => {
      throw new Error('not allowed')
    })

    await expect(copyText('券商原句')).rejects.toThrow('copy failed')
  })

  it('後備會把暫存的 textarea 清乾淨（不留殘骸在 DOM 上）', async () => {
    setSecureContext(false)
    setClipboard(undefined)
    setExecCommand(() => true)

    await copyText('券商原句')
    expect(document.querySelector('textarea')).toBeNull()
  })
})
