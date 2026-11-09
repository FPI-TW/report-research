import { Fragment, useState } from 'react'
import { Button, Card, Field, Segmented, toast } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { Icon } from '../../../components/primitives/Icon'
import { copyText } from '../../../lib/clipboard'
import { curlCommand, plainText, requestPath, type ApiEndpoint } from './apiDoc'
import { sendTry, type TryResult } from './apiTry'
import { Inline } from './ApiDocView'
import d from './ApiDoc.module.css'
import { t } from '@/i18n'

const toneOf = (status: number) => (status < 300 ? 'ok' : status === 429 || status >= 500 ? 'warn' : 'bad')

/**
 * 「試打」分頁：端點與參數表單都從說明文件解析而來（`endpointsOf`），文件寫什麼這裡就能打什麼。
 *
 * 金鑰只放在這個元件的 state：不寫 localStorage、不放網址，換分頁或離開就消失；畫面上的 curl 指令
 * 一律用 `$API_KEY` 代替。試打是真的請求，會計入該用戶端的限流與每日額度，也會更新「最後使用」。
 *
 * 只列 GET 端點：上傳（POST）要送檔案內容，表單送不出來，改看說明文件裡的 curl 範例。
 */
export function ApiTester({ endpoints: all, baseUrl }: { endpoints: readonly ApiEndpoint[]; baseUrl: string }) {
  const endpoints = all.filter(ep => ep.method === 'GET')
  const untryable = all.filter(ep => ep.method !== 'GET')
  const [apiKey, setApiKey] = useState('')
  const [picked, setPicked] = useState(0)
  const [values, setValues] = useState<Record<string, Record<string, string>>>({})
  const [result, setResult] = useState<TryResult | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [sending, setSending] = useState(false)

  if (endpoints.length === 0) {
    return <Card><p className={c.empty}>{t('admin.apiClients.apiTester.theDocumentationHasNoEndpoints')}</p></Card>
  }
  const endpoint = endpoints[Math.min(picked, endpoints.length - 1)]
  const key = `${endpoint.method} ${endpoint.path}`
  const current = values[key] ?? {}
  const path = requestPath(endpoint, current)
  const missingPath = endpoint.params.filter(p => p.location === 'path' && !(current[p.name] ?? '').trim())
  const curl = curlCommand(endpoint.method, `${baseUrl}${path ?? endpoint.path}`)

  const setValue = (name: string, value: string) => setValues(v => ({ ...v, [key]: { ...current, [name]: value } }))
  const send = async () => {
    if (!path) return
    setSending(true)
    setFailure(null)
    try {
      setResult(await sendTry(endpoint.method, path, apiKey.trim()))
    } catch {
      setResult(null)
      setFailure(t('admin.apiClients.apiTester.theRequestDidnTGo'))
    } finally {
      setSending(false)
    }
  }
  const copyCurl = () => copyText(curl).then(() => toast(t('admin.apiClients.apiTester.curlCommandCopied')), () => toast(t('admin.apiClients.apiTester.copyFailedSelectItManually'), { tone: 'bad' }))

  return (
    <div className={d.tester}>
      <Card title={t('admin.apiClients.apiTester.request')} labelledBy="api-try-request">
        <form className={`${c.cardBody} ${d.form}`} onSubmit={e => { e.preventDefault(); void send() }}>
          <Field label={t('admin.apiClients.apiTester.apiKey')}>
            <input className={c.input} type="password" value={apiKey} onChange={e => setApiKey(e.target.value)}
              autoComplete="off" spellCheck={false} placeholder={t('admin.apiClients.apiTester.rmkLeaveEmptyToTry')} />
          </Field>
          <p className={c.fieldHint}>
            {t('admin.apiClients.apiTester.theKeyStaysInThis')}
            {untryable.length > 0 && <> {untryable.map(ep => `${ep.method} ${ep.path}`).join(t('admin.apiClients.apiTester.text'))}{' '}{t('admin.apiClients.apiTester.toSendFileContentUse')}</>}
          </p>
          <Segmented label={t('admin.apiClients.apiTester.endpoint')} value={String(endpoints.indexOf(endpoint))} wrap
            options={endpoints.map((ep, i) => ({ value: String(i), label: plainText(ep.path.replace('/external/v1', '')) }))}
            onChange={v => { setPicked(Number(v)); setResult(null); setFailure(null) }} />
          <div>
            <div className={d.endpointPath}>{endpoint.method} {endpoint.path}</div>
            <div className={c.sub}>
              {endpoint.summary && <Inline text={endpoint.summary} />}
              {endpoint.scope && <>{' '}{t('admin.apiClients.apiTester.requiredPermission')}<code>{endpoint.scope}</code></>}
            </div>
          </div>
          {endpoint.params.map(p => (
            <Field key={p.name} label={
              <span className={d.paramName}>
                {p.name}{p.required && <span className={d.required} aria-label={t('admin.apiClients.apiTester.required')}>*</span>}
              </span>
            }>
              <input className={c.input} value={current[p.name] ?? ''} onChange={e => setValue(p.name, e.target.value)}
                placeholder={p.defaultValue ?? ''} spellCheck={false} aria-describedby={`try-${p.name}-hint`} />
              <span id={`try-${p.name}-hint`} className={c.fieldHint}><Inline text={p.description} /></span>
            </Field>
          ))}
          <div className={c.toolbar}>
            <Button variant="primary" type="submit" disabled={sending || !path}>
              <Icon name="send" size={13} />{sending ? t('admin.apiClients.apiTester.sending') : t('admin.apiClients.apiTester.send')}
            </Button>
            {missingPath.length > 0 && <span className={c.muted}>{t('admin.apiClients.apiTester.fillIn')}{' '}{missingPath.map(p => p.name).join(t('admin.apiClients.apiTester.text'))}</span>}
          </div>
        </form>
      </Card>
      <div className={d.result}>
        <Card title={t('admin.apiClients.apiTester.response')} labelledBy="api-try-response">
          <div className={c.cardBody}>
            {failure ? (
              <p className={c.error} role="alert">{failure}</p>
            ) : !result ? (
              <p className={c.muted}>{t('admin.apiClients.apiTester.fillInTheParametersAnd')}</p>
            ) : (
              <div className={d.result} aria-live="polite">
                <div className={d.statusLine}>
                  <span className={d.statusCode} data-tone={toneOf(result.status)}>{result.status}</span>
                  <span>{result.statusText}</span>
                  <span className={c.muted}>{result.ms} ms</span>
                </div>
                {result.headers.length > 0 && (
                  <dl className={d.headers}>
                    {result.headers.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}
                  </dl>
                )}
                <pre className={`${d.pre} ${d.resultBody}`} aria-label={t('admin.apiClients.apiTester.responseBody')}><code>{result.body || t('admin.apiClients.apiTester.empty')}</code></pre>
              </div>
            )}
          </div>
        </Card>
        <Card title="curl" labelledBy="api-try-curl" hint={t('admin.apiClients.apiTester.exportApiKeyFirst')}>
          <div className={`${c.cardBody} ${d.curl}`}>
            <pre className={d.pre} aria-label={t('admin.apiClients.apiTester.curlCommand')}><code>{curl}</code></pre>
            <Button size="sm" onClick={copyCurl}><Icon name="copy" size={13} />{t('admin.apiClients.apiTester.copy')}</Button>
          </div>
        </Card>
      </div>
    </div>
  )
}
