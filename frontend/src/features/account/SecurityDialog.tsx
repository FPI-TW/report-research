import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState, type FormEvent } from 'react'
import { renderSVG } from 'uqr'
import { Modal } from '../../components/primitives/Modal'
import { meSecurityApi, type TotpSetup } from '../../lib/accountSecurityApi'
import { useMe } from '../../lib/useMe'
import { ElevationCancelledError, useElevationGate } from './useElevationGate'
import styles from './Account.module.css'

const TOTP_KEY = ['me', 'totp'] as const

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

/** otpauth URI 的 QR code。固定白底黑碼（深色模式也一樣），驗證器 App 才掃得到。 */
function TotpQr({ uri }: { uri: string }) {
  const svg = useMemo(() => renderSVG(uri, { border: 4, ecc: 'L' }), [uri])
  return (
    <div className={styles.qr} role="img" aria-label="兩步驟驗證的 QR code" data-testid="totp-qr"
      dangerouslySetInnerHTML={{ __html: svg }} />
  )
}

/**
 * 帳號安全設定（帳號選單 →「帳號安全」）：每位使用者自己開關兩步驟驗證（TOTP）。
 *
 * 開啟：產生 secret → 顯示 QR code 給驗證器 App 掃描（在本機以 `uqr` 產生 SVG，otpauth URI 不送到任何外部服務；
 * 掃不了時可展開手動輸入金鑰）→ 輸入一次正確的驗證碼才真的啟用。關閉要先重新驗證（密碼＋驗證碼）。
 * 規則都在後端（`/api/me/totp*`），這裡只是流程。
 */
export function SecurityDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const client = useQueryClient()
  const me = useMe()
  const status = useQuery({ queryKey: TOTP_KEY, queryFn: meSecurityApi.totpStatus, enabled: open, retry: false })
  const [setup, setSetup] = useState<TotpSetup | null>(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const { guard, dialog, open: elevating } = useElevationGate(meSecurityApi.elevate, true)

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: TOTP_KEY }),
      client.invalidateQueries({ queryKey: ['me'] }),
    ])
  }
  const close = () => { setSetup(null); setCode(''); setError(null); setNotice(null); onClose() }

  const run = async (fn: () => Promise<void>) => {
    setError(null)
    setNotice(null)
    setBusy(true)
    try {
      await fn()
    } catch (err) {
      if (!(err instanceof ElevationCancelledError)) setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  const begin = () => run(async () => {
    setSetup(await meSecurityApi.totpSetup())
    setCode('')
  })
  const confirm = (e: FormEvent) => {
    e.preventDefault()
    void run(async () => {
      await meSecurityApi.totpConfirm(code.trim())
      setSetup(null)
      setCode('')
      setNotice('已開啟兩步驟驗證。之後登入與敏感操作都需要輸入驗證碼。')
      await refresh()
    })
  }
  const disable = () => run(async () => {
    await guard(() => meSecurityApi.totpDisable())
    setNotice('已關閉兩步驟驗證。')
    await refresh()
  })

  const enabled = status.data?.enabled ?? me.data?.totp_enabled ?? false

  return (
    <>
      <Modal open={open} onClose={() => { if (!elevating) close() }} title="帳號安全" className={styles.modal}>
        <div className={styles.form}>
          <p className={styles.status}>
            兩步驟驗證
            <span className={`${styles.badge} ${enabled ? '' : styles.badgeOff}`}>{enabled ? '已開啟' : '未開啟'}</span>
          </p>
          {!setup && (
            <p className={styles.hint}>
              開啟後，登入與敏感操作除了密碼，還要輸入驗證器 App（Google Authenticator、Microsoft Authenticator、
              1Password 等）顯示的 6 位數驗證碼。
            </p>
          )}
          {status.isError && <p className={styles.error} role="alert">狀態載入失敗：{messageOf(status.error)}</p>}
          {notice && <p className={styles.ok} role="status">{notice}</p>}
          {setup ? (
            <form className={styles.form} onSubmit={confirm} aria-label="確認兩步驟驗證">
              <div className={styles.setup}>
                <TotpQr uri={setup.otpauth_uri} />
                <div className={styles.steps}>
                  <p className={styles.step}><span className={styles.stepNo}>1</span>用驗證器 App 掃描 QR code</p>
                  <details className={styles.manual}>
                    <summary>無法掃描？改用手動輸入金鑰</summary>
                    <p className={styles.secret} data-testid="totp-secret">{setup.secret}</p>
                  </details>
                  <a className={styles.openApp} href={setup.otpauth_uri}>在這支手機上開啟驗證器</a>
                  <label className={styles.field}>
                    <span className={styles.step}><span className={styles.stepNo}>2</span>輸入 App 顯示的 6 位數驗證碼</span>
                    <input value={code} onChange={e => setCode(e.target.value)} inputMode="numeric"
                      autoComplete="one-time-code" required maxLength={7} pattern="[0-9 ]*" aria-label="驗證碼" />
                  </label>
                </div>
              </div>
              <p className={styles.hint}>QR code 與金鑰只顯示這一次，關掉視窗後要重新設定。</p>
              {error && <p className={styles.error} role="alert">{error}</p>}
              <div className={styles.actions}>
                <button type="button" className={styles.secondary} onClick={() => { setSetup(null); setError(null) }}>
                  取消
                </button>
                <button type="submit" className={styles.primary} disabled={busy}>確認並開啟</button>
              </div>
            </form>
          ) : (
            <>
              {error && <p className={styles.error} role="alert">{error}</p>}
              <div className={styles.actions}>
                {enabled ? (
                  <button type="button" className={`${styles.secondary} ${styles.danger}`} disabled={busy}
                    onClick={() => void disable()}>關閉兩步驟驗證</button>
                ) : (
                  <button type="button" className={styles.primary} disabled={busy || status.isPending}
                    onClick={() => void begin()}>開啟兩步驟驗證</button>
                )}
              </div>
            </>
          )}
        </div>
      </Modal>
      {dialog}
    </>
  )
}
