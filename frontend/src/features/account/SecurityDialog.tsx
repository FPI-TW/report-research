import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Modal } from '../../components/primitives/Modal'
import { meSecurityApi, type TotpSetup } from '../../lib/accountSecurityApi'
import { useMe } from '../../lib/useMe'
import { ElevationCancelledError, useElevationGate } from './useElevationGate'
import styles from './Account.module.css'

const TOTP_KEY = ['me', 'totp'] as const

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

/**
 * 帳號安全設定（帳號選單 →「帳號安全」）：每位使用者自己開關兩步驟驗證（TOTP）。
 *
 * 開啟：產生 secret → 顯示 otpauth URI 與 secret 文字（驗證器 App 可貼上或手動輸入；刻意不畫 QR code，
 * 免得為此新增相依）→ 輸入一次正確的驗證碼才真的啟用。關閉要先重新驗證（密碼＋驗證碼）。
 * 規則都在後端（`/api/me/totp*`），這裡只是流程。
 *
 * 管理員 TOTP 強制政策開啟時（`/api/me` 的 `mfa_policy_locked`），管理員看不到「關閉」按鈕、改顯示一行說明；
 * 擋人的是後端（403 `mfa_policy_locked`），這裡只是不讓人按一個注定失敗的按鈕。
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
  const policyLocked = me.data?.mfa_policy_locked === true

  return (
    <>
      <Modal open={open} onClose={() => { if (!elevating) close() }} title="帳號安全">
        <div className={styles.form}>
          <p className={styles.status}>
            兩步驟驗證
            <span className={`${styles.badge} ${enabled ? '' : styles.badgeOff}`}>{enabled ? '已開啟' : '未開啟'}</span>
          </p>
          <p className={styles.hint}>
            開啟後，登入時除了密碼還要輸入驗證器 App（Google Authenticator、Microsoft Authenticator、1Password 等）
            顯示的 6 位數驗證碼。每組驗證碼只能用一次。
          </p>
          {status.isError && <p className={styles.error} role="alert">狀態載入失敗：{messageOf(status.error)}</p>}
          {notice && <p className={styles.ok} role="status">{notice}</p>}
          {setup ? (
            <form className={styles.form} onSubmit={confirm} aria-label="確認兩步驟驗證">
              <p className={styles.label}>1. 在驗證器 App 新增帳號，貼上這個 otpauth 連結：</p>
              <p className={styles.secret} data-testid="otpauth-uri">{setup.otpauth_uri}</p>
              <p className={styles.label}>或手動輸入金鑰：</p>
              <p className={styles.secret} data-testid="totp-secret">{setup.secret}</p>
              <label className={styles.field}>2. 輸入 App 顯示的驗證碼
                <input value={code} onChange={e => setCode(e.target.value)} inputMode="numeric"
                  autoComplete="one-time-code" required maxLength={7} pattern="[0-9 ]*" />
              </label>
              <p className={styles.hint}>金鑰只顯示這一次；關掉這個視窗後要重新開始設定。</p>
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
                {enabled && policyLocked ? (
                  <p className={styles.hint} data-testid="totp-policy-locked">
                    管理員帳號在強制兩步驟驗證政策下不可自行關閉。遺失驗證器時，請由 super admin 或主機上的
                    {' '}<code>create_admin.py --reset-totp</code> 重設。
                  </p>
                ) : enabled ? (
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
