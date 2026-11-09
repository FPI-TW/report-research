import { Button, Dialog, DialogActions, DialogBody, toast } from '../../../components/console'
import { Icon } from '../../../components/primitives/Icon'
import { copyText } from '../../../lib/clipboard'
import local from '../ApiClients.module.css'
import { t } from '@/i18n'

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
      () => toast(t('admin.apiClients.keyDialog.keyCopied')),
      () => toast(t('admin.apiClients.keyDialog.copyFailedSelectTheKey'), { tone: 'bad' }),
    )
  }
  return (
    <Dialog open={shown != null} onClose={onClose} title={shown ? t('admin.apiClients.keyDialog.newKeyFor', { name: shown.name }) : ''} width={500} dismissible={false}>
      <DialogBody>{t('admin.apiClients.keyDialog.thisKeyIsShownOnly')}</DialogBody>
      <div className={local.keyField}>
        <code className={local.keyBox} aria-label={t('admin.apiClients.keyDialog.apiKey')}>{shown?.key}</code>
        <Button className={local.keyCopy} onClick={copy} data-autofocus><Icon name="copy" size={14} />{t('admin.apiClients.keyDialog.copy')}</Button>
      </div>
      <DialogActions>
        <Button variant="primary" onClick={onClose}>{t('admin.apiClients.keyDialog.copiedClose')}</Button>
      </DialogActions>
    </Dialog>
  )
}
