import { Button, Dialog, toast } from '../../../components/console'
import { Icon } from '../../../components/primitives/Icon'
import { copyText } from '../../../lib/clipboard'
import local from '../ApiClients.module.css'

export interface ShownKey { name: string; key: string }

/**
 * 原始金鑰的一次性對話框。金鑰只活在這個元件的 props（父層 state）裡：關閉後父層把它清成 null，
 * 前端沒有任何地方能再取得——後端只存 hash，遺失只能輪替。
 * 只能按「我已複製，關閉」離開：Esc 與點遮罩都不關，免得還沒複製就手滑關掉。
 */
export function KeyDialog({ shown, onClose }: { shown: ShownKey | null; onClose: () => void }) {
  // 複製失敗時照實說（區網的非安全情境可能失敗）；金鑰框可整段選取，手動複製仍可行。
  const copy = () => {
    if (!shown) return
    copyText(shown.key).then(
      () => toast('已複製金鑰'),
      () => toast('複製失敗，請手動選取金鑰', { tone: 'bad' }),
    )
  }
  return (
    <Dialog open={shown != null} onClose={onClose} title={shown ? `「${shown.name}」的新金鑰` : ''} width={500} locked>
      <p className={local.keyNote}>這把金鑰只會顯示這一次。請現在複製，交給對方後再關閉。</p>
      <div className={local.keyField}>
        <code className={local.keyBox} aria-label="API 金鑰">{shown?.key}</code>
        <Button className={local.keyCopy} onClick={copy} data-autofocus><Icon name="copy" size={14} />複製</Button>
      </div>
      <div className={local.keyActions}>
        <Button variant="primary" onClick={onClose}>我已複製，關閉</Button>
      </div>
    </Dialog>
  )
}
