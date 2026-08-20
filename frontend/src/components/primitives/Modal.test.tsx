import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitForElementToBeRemoved } from '@testing-library/react'
import { Modal } from './Modal'

/* 壓克力面板的成立條件是「面板不是 backdrop-filter 元素的後代」。破壞它不會有任何
   錯誤：只會讓 .panel 的 blur 靜默失效，面板退化成一張半透明片（背景色帶邊界清晰、
   背後文字讀得出來）。合併 .scrim/.scrimLayer 兩條規則是最可能的破壞方式，故直接對
   原始 CSS 斷言——`getComputedStyle` 在 jsdom 認不得 backdrop-filter。 */
const modalCss = Object.values(
  import.meta.glob('./Modal.module.css', { eager: true, import: 'default', query: '?raw' }),
)[0] as string

function rules(css: string): { sel: string; body: string }[] {
  return [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map(m => ({ sel: m[1].trim(), body: m[2] }))
}
const decl = (sel: string) =>
  rules(modalCss).filter(r => r.sel === sel).map(r => r.body).join(';')

describe('Modal', () => {
  it('open=false 不渲染內容', () => {
    render(<Modal open={false} onClose={() => {}}>內容</Modal>)
    expect(screen.queryByText('內容')).toBeNull()
  })
  it('open=true 渲染 dialog + title + 內容', () => {
    render(<Modal open onClose={() => {}} title="標題">內容</Modal>)
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByText('標題')).toBeTruthy()
    expect(screen.getByText('內容')).toBeTruthy()
  })
  it('Esc 觸發 onClose', () => {
    const onClose = vi.fn()
    render(<Modal open onClose={onClose}>x</Modal>)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
  it('open→false 後離場卸載（dialog 移除）', async () => {
    const { rerender } = render(<Modal open onClose={() => {}}>x</Modal>)
    expect(screen.getByRole('dialog')).toBeTruthy()
    rerender(<Modal open={false} onClose={() => {}}>x</Modal>)
    await waitForElementToBeRemoved(() => screen.queryByRole('dialog'))
  })
  it('點遮罩關閉、點面板不關閉', () => {
    const onClose = vi.fn()
    render(<Modal open onClose={onClose} title="t">body</Modal>)
    fireEvent.click(screen.getByText('body'))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(document.body.querySelector('[data-scrim]')!)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  // 缺陷：`.scrim` 是 position: fixed，而 fixed 的定位基準不必然是視窗——祖先只要有
  // backdrop-filter／filter／transform 就會成為它的 containing block。側欄（SideRail
  // 的 .rail）正是磨砂玻璃且 overflow: hidden，所以就地渲染的彈窗會被鎖在 272px 寬的
  // 側欄裡、貼著畫面最左側並被裁掉。jsdom 不算版面，故這裡釘的是**修法本身**：
  // 節點必須掛在 document.body 底下、而不是呼叫端的子樹裡。
  it('遮罩的 backdrop-filter 住在 .scrimLayer，.scrim 上不得有（否則面板壓克力失效）', () => {
    expect(rules(modalCss).length).toBeGreaterThan(5)   // 反向自保：regex 掛掉時上面兩條會空過
    expect(decl('.scrim')).not.toMatch(/backdrop-filter/)
    expect(decl('.scrimLayer')).toMatch(/backdrop-filter:\s*var\(--tf-scrim-filter\)/)
    // 面板要有自己的模糊，且底色走壓克力 token（不是給常駐 chrome 的 --tf-glass-*）
    expect(decl('.panel')).toMatch(/backdrop-filter:\s*var\(--tf-acrylic-filter\)/)
    expect(decl('.panel')).toMatch(/background:\s*var\(--tf-acrylic\)/)
    // .scrimLayer 絕對定位，面板若維持 static 會被遮罩蓋住
    expect(decl('.panel')).toMatch(/position:\s*relative/)
  })

  it('遮罩層與面板是同層兄弟，面板不在遮罩層之內', () => {
    render(<Modal open onClose={() => {}} title="t">body</Modal>)
    const layer = document.body.querySelector('[data-scrim-layer]')!
    const panel = document.body.querySelector('[role="dialog"]')!
    expect(layer).toBeTruthy()
    expect(layer.contains(panel)).toBe(false)
    expect(layer.parentElement).toBe(panel.parentElement)
  })

  it('掛在 document.body（不留在呼叫端子樹內）', () => {
    const { container } = render(
      <div data-host style={{ backdropFilter: 'blur(10px)', overflow: 'hidden' }}>
        <Modal open onClose={() => {}} title="t">body</Modal>
      </div>,
    )
    expect(container.querySelector('[data-host]')).toBeTruthy()
    expect(container.querySelector('[data-scrim]')).toBeNull()
    const scrim = document.body.querySelector('[data-scrim]')
    expect(scrim).toBeTruthy()
    expect(scrim!.closest('[data-host]')).toBeNull()
  })
})
