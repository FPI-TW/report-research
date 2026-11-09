import { describe, expect, it } from 'vitest'
import ts from 'typescript'
import zhApp from './messages/zh-Hant/app'
import zhAdmin from './messages/zh-Hant/admin'
import enApp from './messages/en/app'
import enAdmin from './messages/en/admin'
import type { Message } from '.'

/**
 * 介面字串的守門（src/i18n/index.ts 的規則）：
 * 1. 兩個語系的字典鍵完全一致、內插變數一致；`admin.` 開頭的鍵只在 admin 字典。
 * 2. 元件與模組裡不得出現沒經過字典的 CJK 字串字面值（例外逐條寫在 EXEMPT，附理由）。
 * 3. 不得在模組頂層呼叫 `t()`／`tx()`／`sentences()`（import 期就定值，切換語系後留著舊字）。
 * 4. 管理後台的鍵只出現在管理後台的檔案，而一般頁面不靜態 import 管理後台的檔案（admin 字典與管理頁 chunk 一起載入）。
 */

// 整個 src/ 的原始碼（vite 的 glob，鍵是相對本檔的路徑）；檔名一律改成相對 src/。
const RAW = import.meta.glob<string>(['../**/*.ts', '../**/*.tsx'], { query: '?raw', import: 'default', eager: true })
const FILES: Record<string, string> = Object.fromEntries(Object.entries(RAW).map(([k, v]) => [k.startsWith('./') ? 'i18n/' + k.slice(2) : k.replace(/^\.\.\//, ''), v]))
const CJK = /[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]/

// 第三方匯入（shadcn registry 拉入，eslint.config.js 的忽略清單）與測試輔助：不受這條規則管。
const THIRD_PARTY = [
  'components/animate-ui/', 'components/ui/', 'components/fluid-hover-highlight.tsx',
  'hooks/use-controlled-state.tsx', 'hooks/use-is-in-view.tsx', 'hooks/use-fluid-hover.ts', 'hooks/use-merge-split.tsx',
  'hooks/use-touch-primary.tsx', 'lib/get-strict-context.tsx', 'lib/elevated.tsx', 'lib/font-weight.ts', 'lib/icon-context.tsx',
  'lib/omit.ts', 'lib/popup.ts', 'lib/shape-context.tsx', 'lib/size-context.tsx', 'lib/springs.ts', 'lib/surface-classes.ts',
  'lib/surface-context.tsx', 'lib/type-scale.ts',
]

/** 不經字典的 CJK 字面值：每一條都是資料或比對鍵，不是介面文字。檔案相對 src/。 */
const EXEMPT: { file: string; text: string; reason: string }[] = [
  { file: 'features/account/SettingsDialog.tsx', text: '中文', reason: '語言選項以該語言自己的名稱顯示（English 同理），兩個語系都不翻' },
  { file: 'lib/useWebSearch.ts', text: '若需要即時數字，可在輸入框的「＋」開啟「網路搜尋」後再問一次；那類回答來自外部網頁，不是研報內容。', reason: '比對後端落庫的提示句（app/services/answer.py 的 TIME_SENSITIVE_WEB_HINT），是資料不是介面' },
  { file: 'features/brief/briefContent.ts', text: '評等', reason: '辨識 LLM 產生的簡報 Markdown 段落標題（資料）' },
  { file: 'features/brief/briefContent.ts', text: '目標價', reason: '辨識 LLM 產生的簡報 Markdown 段落標題（資料）' },
  { file: 'features/brief/briefContent.ts', text: '今日重點', reason: '辨識 LLM 產生的簡報 Markdown 段落標題（資料）' },
  { file: 'features/brief/briefContent.ts', text: '市場焦點', reason: '辨識 LLM 產生的簡報 Markdown 段落標題（資料）' },
  { file: 'features/radar/changeText.ts', text: '沒有更早研報', reason: '比對後端回傳的 diff.note（舊版後端沒有 has_prior_report 時的退路）' },
  { file: 'features/admin/apiClients/apiDoc.ts', text: '參數', reason: '解析 docs/EXTERNAL_API.md（中文文件）的參數表表頭' },
  { file: 'features/admin/apiClients/apiDoc.ts', text: '位置', reason: '解析 docs/EXTERNAL_API.md 的參數表表頭' },
  { file: 'features/admin/apiClients/apiDoc.ts', text: '必填', reason: '解析 docs/EXTERNAL_API.md 的參數表表頭' },
  { file: 'features/admin/apiClients/apiDoc.ts', text: '預設', reason: '解析 docs/EXTERNAL_API.md 的參數表表頭' },
  { file: 'features/admin/apiClients/apiDoc.ts', text: '說明', reason: '解析 docs/EXTERNAL_API.md 的參數表表頭' },
  { file: 'features/admin/apiClients/apiDoc.ts', text: '是', reason: '解析 docs/EXTERNAL_API.md 參數表的「必填」欄值' },
  { file: 'features/admin/ops/pipeline/pipelineTestKit.ts', text: '凱基', reason: '測試共用的 /api/progress 假資料（券商顯示名來自後端）' },
  { file: 'features/admin/ops/pipeline/pipelineTestKit.ts', text: '高盛', reason: '測試共用的 /api/progress 假資料（券商顯示名來自後端）' },
  { file: 'features/admin/ops/pipeline/pipelineTestKit.ts', text: '同步已完成', reason: '測試共用的 /api/progress 假資料（sync log 原文）' },
]

/** posix 路徑正規化（a/b/../c → a/c）。 */
function normalize(p: string): string {
  const out: string[] = []
  for (const seg of p.split('/')) {
    if (seg === '..') out.pop()
    else if (seg && seg !== '.') out.push(seg)
  }
  return out.join('/')
}

function resolveImport(from: string, spec: string): string | null {
  if (spec.startsWith('@/')) return spec.slice(2)
  if (!spec.startsWith('.')) return null
  return normalize(from.split('/').slice(0, -1).join('/') + '/' + spec)
}

const SOURCE_FILES = Object.keys(FILES).sort().filter(f =>
  !/\.test\.(ts|tsx)$/.test(f) && !f.endsWith('.d.ts') && !f.startsWith('test/') && !f.startsWith('i18n/messages/')
  && !THIRD_PARTY.some(p => f === p || (p.endsWith('/') && f.startsWith(p))),
)

function parse(file: string): ts.SourceFile {
  const text = FILES[file]
  return ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
}

function isFunctionLike(n: ts.Node): boolean {
  return ts.isFunctionLike(n) || ts.isPropertyDeclaration(n) || ts.isClassStaticBlockDeclaration(n)
}

function inType(n: ts.Node): boolean {
  for (let p = n.parent; p; p = p.parent) {
    if (ts.isTypeNode(p) && !ts.isExpressionWithTypeArguments(p)) return true
    if (ts.isStatement(p)) return false
  }
  return false
}

/** console.* 的引數是給開發者看的除錯訊息，不是介面。 */
function inConsoleCall(n: ts.Node): boolean {
  for (let p = n.parent; p; p = p.parent) {
    if (ts.isCallExpression(p) && /^console\.\w+$/.test(p.expression.getText())) return true
    if (ts.isStatement(p)) return false
  }
  return false
}

function lineOf(sf: ts.SourceFile, n: ts.Node): number {
  return sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1
}

function cjkLiterals(file: string): { line: number; text: string }[] {
  const sf = parse(file)
  const hits: { line: number; text: string }[] = []
  const visit = (n: ts.Node) => {
    const text = ts.isStringLiteralLike(n) || ts.isJsxText(n) || ts.isTemplateHead(n) || ts.isTemplateMiddle(n) || ts.isTemplateTail(n)
      ? n.text : null
    if (text !== null && CJK.test(text) && !inType(n) && !inConsoleCall(n)) hits.push({ line: lineOf(sf, n), text: text.trim() })
    ts.forEachChild(n, visit)
  }
  visit(sf)
  return hits
}

function placeholders(m: Message): string {
  const forms = typeof m === 'string' ? [m] : [m.one, m.other]
  const names = new Set<string>()
  for (const f of forms) for (const x of f.matchAll(/\{(\w+)\}/g)) names.add(x[1])
  return [...names].sort().join(',')
}

const PAIRS = [
  ['app', zhApp, enApp],
  ['admin', zhAdmin, enAdmin],
] as const

describe('字典', () => {
  it.each(PAIRS)('%s：兩個語系的鍵完全一致', (_ns, zh, en) => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(zh).sort())
  })

  it.each(PAIRS)('%s：每個鍵的內插變數兩邊相同', (_ns, zh, en) => {
    const zhMap = zh as Record<string, Message>
    const enMap = en as Record<string, Message>
    const diff = Object.keys(zhMap).filter(k => placeholders(zhMap[k]) !== placeholders(enMap[k]))
    expect(diff).toEqual([])
  })

  it('admin. 開頭的鍵只在 admin 字典，app 字典沒有', () => {
    expect(Object.keys(zhAdmin).filter(k => !k.startsWith('admin.'))).toEqual([])
    expect(Object.keys(zhApp).filter(k => k.startsWith('admin.'))).toEqual([])
  })

  it('英文字典沒有漏翻（值裡沒有 CJK）', () => {
    // 例外：數值本身就是要照打的中文資料範例（報告類型的值是中文）。
    const allowed = new Set(['admin.apiClients.clientFields.text'])
    const leaks = [...Object.entries(enApp), ...Object.entries(enAdmin)]
      .filter(([k, v]) => !allowed.has(k) && CJK.test(typeof v === 'string' ? v : v.one + v.other))
      .map(([k]) => k)
    expect(leaks).toEqual([])
  })

  it('英文介面不用「·」當分隔符（產品 UI 規則）', () => {
    const bad = [...Object.entries(enApp), ...Object.entries(enAdmin)]
      .filter(([, v]) => (typeof v === 'string' ? v : v.one + v.other).includes('·'))
      .map(([k]) => k)
    expect(bad).toEqual([])
  })
})

describe('介面字串一律經過字典', () => {
  it('元件與模組裡沒有未經字典的 CJK 字串字面值', () => {
    const found: string[] = []
    for (const file of SOURCE_FILES) {
      for (const hit of cjkLiterals(file)) {
        if (EXEMPT.some(e => e.file === file && e.text === hit.text)) continue
        found.push(`${file}:${hit.line} ${JSON.stringify(hit.text)}`)
      }
    }
    // 新的介面字串：在 src/i18n/messages/zh-Hant/ 與 en/ 加同一個鍵，元件裡寫 t('鍵')。
    expect(found).toEqual([])
  })

  it('例外清單沒有過期的項目', () => {
    const stale = EXEMPT.filter(e => !cjkLiterals(e.file).some(h => h.text === e.text))
    expect(stale).toEqual([])
  })

  it('模組頂層不呼叫 t()／tx()／sentences()', () => {
    const found: string[] = []
    for (const file of SOURCE_FILES) {
      if (file.startsWith('i18n/')) continue
      const sf = parse(file)
      const visit = (n: ts.Node, inFn: boolean) => {
        if (!inFn && ts.isCallExpression(n) && ts.isIdentifier(n.expression) && ['t', 'tx', 'sentences'].includes(n.expression.text)) {
          found.push(`${file}:${lineOf(sf, n)}`)
        }
        ts.forEachChild(n, c => visit(c, inFn || isFunctionLike(n)))
      }
      visit(sf, false)
    }
    expect(found).toEqual([])
  })
})

describe('admin namespace 的邊界', () => {
  const isAdminFile = (f: string) =>
    f.startsWith('features/admin/') || f.startsWith('components/console/') || /^components\/shell\/(Admin|adminNav)/.test(f)

  it('admin. 的鍵只用在管理後台的檔案', () => {
    const bad = SOURCE_FILES.filter(f => !isAdminFile(f) && /\bt\(\s*'admin\./.test(FILES[f]))
    expect(bad).toEqual([])
  })

  it('一般頁面不靜態 import 管理後台的檔案（只能經 route loader 的動態 import）', () => {
    const bad: string[] = []
    for (const file of SOURCE_FILES.filter(f => !isAdminFile(f))) {
      const sf = parse(file)
      for (const st of sf.statements) {
        if (!ts.isImportDeclaration(st) || st.importClause?.phaseModifier === ts.SyntaxKind.TypeKeyword) continue
        const spec = (st.moduleSpecifier as ts.StringLiteral).text
        const target = resolveImport(file, spec)
        if (target && isAdminFile(target)) bad.push(`${file} → ${spec}`)
      }
    }
    expect(bad).toEqual([])
  })
})
