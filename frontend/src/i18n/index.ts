import { Fragment, createElement, useSyncExternalStore, type ReactNode } from 'react'
import type AppMessages from './messages/zh-Hant/app'
import type AdminMessages from './messages/zh-Hant/admin'

/**
 * 介面語系（中英）：字典、語言偏好與 Intl 格式化。刻意自製、不引入 i18next：需要的只有鍵查表、`{name}` 內插、
 * 英文單複數與依語系 lazy 載入字典，一百多行就涵蓋；零相依（不必過授權守門、不加 bundle），鍵由 TypeScript 檢查。
 *
 * ## 語言偏好（介面與問答輸出共用一個）
 *
 * 偏好沿用 M10b 的 `tf.locale`（localStorage）：改動前它只決定問答輸出語言，現在介面也跟著它，舊偏好照樣有效。
 * 另寫一份 cookie `tf_locale`，給在 SPA 之外、由伺服器渲染的登入頁讀（`web/routers/auth_pages.py`）。
 * 初始值依序：使用者偏好（localStorage，再來 cookie）→ profile 預設（`/api/branding` 的 `locale.default`）→ `zh-Hant`；
 * 偏好不在 profile 開放的語系裡時改用 profile 預設。
 *
 * ## 字典
 *
 * `messages/<語系>/<namespace>.ts`，扁平的鍵 → 字串。namespace 有兩個：`app`（研報平台與共用元件，啟動時載入）、
 * `admin`（管理後台，鍵以 `admin.` 開頭；與管理後台的 chunk 一起載入，一般使用者不下載）。兩個語系的鍵必須完全
 * 一致：英文字典的型別由中文字典推得（缺鍵、多鍵 tsc 就紅），`i18n.test.ts` 再逐鍵對帳一次。
 *
 * 英文需要單複數時，值寫成 `{ one, other }`：依 `count`（沒有就取第一個數字變數）以 `Intl.PluralRules` 選。
 *
 * ## 切換
 *
 * `setLocale()` 先載入新語系已用到的 namespace，才通知訂閱者；`main.tsx` 的 `<LocaleBoundary>` 以語系為 key
 * 重掛整棵畫面樹（QueryClient 的快取在它外面，不會丟），所以 `t()` 是普通函式、在 render 期間呼叫即可，不必每個
 * 元件訂閱。**不可在模組頂層呼叫 `t()`**（import 期就定值，切換語系後留著舊字）：模組級的標籤表寫成 getter 或函式
 * （`i18n.test.ts` 以 AST 守門）。
 */

export type Locale = 'zh-Hant' | 'en'
export const LOCALES: readonly Locale[] = ['zh-Hant', 'en']
export const DEFAULT_LOCALE: Locale = 'zh-Hant'

export type Namespace = 'app' | 'admin'
export type PluralMessage = { one: string; other: string }
export type Message = string | PluralMessage
export type Dict = Record<string, Message>
/** 英文字典的型別：鍵與中文字典逐一相同。 */
export type Messages<T> = { [K in keyof T]: Message }

export type MessageKey = Extract<keyof typeof AppMessages | keyof typeof AdminMessages, string>
export type Vars = Record<string, string | number>

const STORAGE_KEY = 'tf.locale'
const COOKIE_KEY = 'tf_locale'

type Loader = () => Promise<{ default: Dict }>
const LOADERS: Record<Locale, Record<Namespace, Loader>> = {
  'zh-Hant': {
    app: () => import('./messages/zh-Hant/app'),
    admin: () => import('./messages/zh-Hant/admin'),
  },
  en: {
    app: () => import('./messages/en/app'),
    admin: () => import('./messages/en/admin'),
  },
}

const dicts: Record<Locale, Partial<Record<Namespace, Dict>>> = { 'zh-Hant': {}, en: {} }
const pending = new Map<string, Promise<void>>()
/** 已經用到的 namespace：切換語系時這些都要先載好新語系的版本。 */
const used = new Set<Namespace>(['app'])

export function isLocale(value: unknown): value is Locale {
  return value === 'zh-Hant' || value === 'en'
}

function readCookie(): string | null {
  try {
    const hit = document.cookie.split(';').map(s => s.trim()).find(s => s.startsWith(`${COOKIE_KEY}=`))
    return hit ? decodeURIComponent(hit.slice(COOKIE_KEY.length + 1)) : null
  } catch {
    return null
  }
}

/** 使用者自己選過的語系（localStorage，再來 cookie）；沒選過回 null。 */
export function storedLocale(): Locale | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (isLocale(v)) return v
  } catch {
    /* 私密模式等：退到 cookie */
  }
  const c = readCookie()
  return isLocale(c) ? c : null
}

function persist(next: Locale): void {
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    /* ignore */
  }
  try {
    document.cookie = `${COOKIE_KEY}=${encodeURIComponent(next)}; path=/; max-age=31536000; SameSite=Lax`
  } catch {
    /* ignore */
  }
}

let current: Locale = storedLocale() ?? DEFAULT_LOCALE
let available: readonly Locale[] = LOCALES
const listeners = new Set<() => void>()

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

function getSnapshot(): Locale {
  return current
}

/** 語系變動時通知（非 React 的訂閱者：頁面標題等）。 */
export function subscribeLocale(cb: () => void): () => void {
  return subscribe(cb)
}

function commit(next: Locale): void {
  current = next
  try {
    document.documentElement.lang = next
  } catch {
    /* ignore */
  }
  listeners.forEach(l => l())
}

/** 直接放進字典（測試的 setup 同步登記全部字典；正式環境走 lazy loader）。 */
export function registerMessages(locale: Locale, ns: Namespace, dict: Dict): void {
  dicts[locale][ns] = dict
}

function loaded(locale: Locale, ns: Namespace): boolean {
  return dicts[locale][ns] !== undefined
}

function loadOne(locale: Locale, ns: Namespace): Promise<void> {
  if (loaded(locale, ns)) return Promise.resolve()
  const id = `${locale}/${ns}`
  let p = pending.get(id)
  if (!p) {
    p = LOADERS[locale][ns]().then(
      m => { dicts[locale][ns] = m.default },
      err => { pending.delete(id); throw err },
    )
    pending.set(id, p)
  }
  return p
}

function loadAll(locale: Locale): Promise<void> {
  return Promise.all([...used].map(ns => loadOne(locale, ns))).then(() => undefined)
}

/** 確保某個 namespace 的目前語系字典已載入（管理後台的 route loader 呼叫 `admin`）。 */
export function ensureNamespace(ns: Namespace): Promise<void> {
  used.add(ns)
  return loadOne(current, ns)
}

export function getLocale(): Locale {
  return current
}

/** profile 開放的語系（`/api/branding` 的 `locale.available`；抓到之前是全部）。 */
export function availableLocales(): readonly Locale[] {
  return available
}

/**
 * 切換語系（可 await）：寫入偏好、載入新語系用到的字典，載好才通知（字典都已在記憶體時同步完成）。
 * 字典載入失敗時保留原語系，不留下一半中文一半英文的畫面。
 */
export function changeLocale(next: Locale): Promise<void> {
  if (next === current) return Promise.resolve()
  persist(next)
  if ([...used].every(ns => loaded(next, ns))) {
    commit(next)
    return Promise.resolve()
  }
  return loadAll(next).then(() => commit(next), err => { console.warn('語系字典載入失敗', err) })
}

/** 同 `changeLocale`，不回傳 promise（事件處理器與既有呼叫端用；字典已在記憶體時同步生效）。 */
export function setLocale(next: Locale): void {
  void changeLocale(next)
}

export function useLocale(): Locale {
  return useSyncExternalStore(subscribe, getSnapshot, () => DEFAULT_LOCALE)
}

/**
 * 啟動：決定初始語系並載好 `app` 字典再開始畫（`main.tsx`）。
 *
 * 有使用者偏好時不等 profile（直接載偏好的字典）；沒有時等 `/api/branding` 回來取 profile 預設——
 * 最多等 `waitMs`，逾時或抓不到就用 `zh-Hant`。profile 回來後偏好不在開放清單裡，就切到 profile 預設。
 */
export async function initI18n(
  profile: Promise<{ defaultLocale?: Locale; availableLocales?: readonly Locale[] } | null | undefined>,
  waitMs = 1500,
): Promise<void> {
  const applyProfile = (p: Awaited<typeof profile>): Locale => {
    if (p?.availableLocales?.length) available = p.availableLocales
    const fallback = p?.defaultLocale && available.includes(p.defaultLocale) ? p.defaultLocale : available[0]
    return fallback ?? DEFAULT_LOCALE
  }
  const stored = storedLocale()
  if (stored) {
    void profile.then(p => {
      const fallback = applyProfile(p)
      if (!available.includes(current)) setLocale(fallback)
    }, () => undefined)
    await loadOne(stored, 'app').catch(() => loadOne(DEFAULT_LOCALE, 'app').then(() => { current = DEFAULT_LOCALE }))
    commit(current)
    return
  }
  const timeout = new Promise<null>(resolve => setTimeout(() => resolve(null), waitMs))
  const p = await Promise.race([profile.catch(() => null), timeout])
  const initial = applyProfile(p)
  try {
    await loadOne(initial, 'app')
    current = initial
  } catch {
    await loadOne(DEFAULT_LOCALE, 'app').catch(() => undefined)
    current = DEFAULT_LOCALE
  }
  commit(current)
}

function lookup(key: string): Message | undefined {
  const ns: Namespace = key.startsWith('admin.') ? 'admin' : 'app'
  return dicts[current][ns]?.[key] ?? dicts[DEFAULT_LOCALE][ns]?.[key]
}

function pluralCount(vars: Vars | undefined): number {
  if (!vars) return 0
  if (typeof vars.count === 'number') return vars.count
  for (const v of Object.values(vars)) if (typeof v === 'number') return v
  return 0
}

const pluralRules = new Map<Locale, Intl.PluralRules>()

function interpolate(text: string, vars: Vars | undefined): string {
  if (!vars) return text
  return text.replace(/\{(\w+)\}/g, (m, name: string) => (name in vars ? String(vars[name]) : m))
}

/** 字典裡有沒有這個鍵（鍵由執行期資料組出來時用，例如後端錯誤代碼）。 */
export function hasKey(key: string): key is MessageKey {
  return lookup(key) !== undefined
}

/** 查字典。找不到的鍵回鍵本身（tsc 擋得住寫錯的鍵；這是執行期的最後一道）。 */
export function t(key: MessageKey, vars?: Vars): string {
  const msg = lookup(key)
  if (msg === undefined) return key
  if (typeof msg === 'string') return interpolate(msg, vars)
  let rules = pluralRules.get(current)
  if (!rules) {
    rules = new Intl.PluralRules(intlLocale())
    pluralRules.set(current, rules)
  }
  return interpolate(rules.select(pluralCount(vars)) === 'one' ? msg.one : msg.other, vars)
}

/**
 * 含元素的句子：字典裡以 `<0>…</0>` 標出要換成元素的片段，`parts[0]` 收到片段文字回傳元素。
 * 例：`'共 <0>{n}</0> 篇'` → `tx('k', [c => <strong>{c}</strong>], { n })`。語序由譯文決定，不必拆成幾段。
 */
export function tx(key: MessageKey, parts: ((chunk: string) => ReactNode)[], vars?: Vars): ReactNode {
  const text = t(key, vars)
  const out: ReactNode[] = []
  const re = /<(\d+)>(.*?)<\/\1>/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const render = parts[Number(m[1])]
    out.push(createElement(Fragment, { key: out.length }, render ? render(m[2]) : m[2]))
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return createElement(Fragment, null, ...out)
}

/** 把幾句話接成一段：中文直接相連，英文以空格分隔（說明文字由多個鍵組成時用）。 */
export function sentences(...parts: string[]): string {
  return parts.join(current === 'en' ? ' ' : '')
}

// ── Intl 格式化（時區語意固定台北，與改動前相同；只有語系跟著介面） ─────────────

export const TIME_ZONE = 'Asia/Taipei'

/** 介面語系 → Intl 的 locale tag（數字分位、日期月份名稱）。 */
export function intlLocale(locale: Locale = current): string {
  return locale === 'en' ? 'en-US' : 'zh-TW'
}

export function formatNumber(n: number, opts?: Intl.NumberFormatOptions): string {
  return n.toLocaleString(intlLocale(), opts)
}

export function formatDate(value: Date | string | number, opts: Intl.DateTimeFormatOptions): string {
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return new Intl.DateTimeFormat(intlLocale(), { timeZone: TIME_ZONE, ...opts }).format(d)
}

/**
 * 日曆日（年月日數字，無時區）的英文寫法：`formatCalendar(2026, 10, 28, { month: 'short', day: 'numeric' })` → `Oct 28`。
 * 只給英文介面用：中文沿用字典裡的「2026 年 10 月」格式（Intl 的 zh-TW 寫法不帶空格，與改動前不同）。
 */
export function formatCalendar(y: number, m: number, d: number, opts: Intl.DateTimeFormatOptions): string {
  return new Intl.DateTimeFormat(intlLocale(), { timeZone: 'UTC', ...opts }).format(new Date(Date.UTC(y, m - 1, d)))
}

/** 依語系排序字串（表格排序、清單）。 */
export function compareText(a: string, b: string): number {
  return a.localeCompare(b, current === 'en' ? 'en' : 'zh-Hant')
}

/** 以語系為 key 重掛整棵樹：切換語系後每個元件重新 render、重新呼叫 `t()`。 */
export function LocaleBoundary({ children }: { children: ReactNode }) {
  const locale = useLocale()
  return createElement(Fragment, { key: locale }, children)
}
