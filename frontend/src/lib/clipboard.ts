/**
 * 複製到剪貼簿，非安全情境退回 `execCommand`。
 *
 * **為什麼不能直接用 `navigator.clipboard`**：本站的區網入口是
 * `http://192.168.1.128:8097/` —— HTTP ＋ 私有 IP 屬**非安全情境**，那裡
 * `navigator.clipboard` 是 `undefined`（不是「呼叫後被拒絕」，是整個 API 不存在）。
 * 直接呼叫的路徑對區網使用者一律靜默失敗：沒有例外、沒有回饋，使用者只會發現貼不出來。
 * 同事幾乎都走區網那個網址，所以那不是邊緣情況，是主要情境。
 *
 * EmbedPDF 內建的 `CopyToClipboard` 工具正是直接呼叫 `navigator.clipboard.writeText`，
 * 這也是 `PdfViewer` 刻意改註冊基礎版 `SelectionPluginPackage`（而非 `/react` 版）
 * 並自理複製的原因。
 *
 * 失敗時 reject，呼叫端才有機會告訴使用者「請手動複製」——吞掉等於再造一次同樣的坑。
 */
export function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text)
  return new Promise((resolve, reject) => {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.top = '-9999px'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    let ok: boolean
    try {
      ok = document.execCommand('copy')
    } catch {
      ok = false
    }
    document.body.removeChild(ta)
    if (ok) resolve()
    else reject(new Error('copy failed'))
  })
}
