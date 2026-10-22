import { Fragment, useMemo, useRef, type ReactNode } from 'react'
import { Button, Card, toast } from '../../../components/console'
import { Icon } from '../../../components/primitives/Icon'
import { copyText } from '../../../lib/clipboard'
import { parseInline, parseMarkdown, type Block } from './apiDoc'
import d from './ApiDoc.module.css'

/** 下載給 API 用戶端的檔名。 */
export const DOC_FILENAME = 'research-external-api-v1.md'

export function Inline({ text }: { text: string }) {
  return (
    <>
      {parseInline(text).map((t, i) => {
        if (t.kind === 'code') return <code key={i}>{t.text}</code>
        if (t.kind === 'strong') return <strong key={i}>{t.text}</strong>
        if (t.kind === 'link') return <a key={i} href={t.href} target="_blank" rel="noreferrer">{t.text}</a>
        return <Fragment key={i}>{t.text}</Fragment>
      })}
    </>
  )
}

function BlockView({ block, index }: { block: Block; index: number }): ReactNode {
  switch (block.kind) {
    case 'heading': {
      // 頁面本身已有 h1：文件的 # 對到 h2，依此類推。
      const level = Math.min(block.level + 1, 6)
      const Tag = `h${level}` as 'h2' | 'h3' | 'h4' | 'h5'
      return <Tag className={d[`h${level}`]} data-doc-heading={index}><Inline text={block.text} /></Tag>
    }
    case 'para':
      return <p><Inline text={block.text} /></p>
    case 'list': {
      const Tag = block.ordered ? 'ol' : 'ul'
      return <Tag>{block.items.map((item, i) => <li key={i}><Inline text={item} /></li>)}</Tag>
    }
    case 'code':
      return <pre className={d.pre}><code>{block.text}</code></pre>
    case 'table':
      return (
        <div className={d.tableWrap}>
          <table>
            <thead><tr>{block.head.map((h, i) => <th key={i}><Inline text={h} /></th>)}</tr></thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>{block.head.map((_, i) => <td key={i}><Inline text={row[i] ?? ''} /></td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )
  }
}

/** 瀏覽器下載一份文字檔。 */
function saveText(filename: string, text: string) {
  const href = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }))
  try {
    const a = document.createElement('a')
    a.href = href
    a.download = filename
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    setTimeout(() => URL.revokeObjectURL(href), 0)
  }
}

/**
 * 「說明文件」分頁：渲染對外 API 說明（站台網址已代入），可複製或下載 Markdown 交給 API 用戶端。
 * 上方的目錄只列 `##` 章節，點了捲到該處（不改網址，`?tab=` 保持不動）。
 */
export function ApiDocView({ markdown, baseUrl }: { markdown: string; baseUrl: string }) {
  const blocks = useMemo(() => parseMarkdown(markdown), [markdown])
  const body = useRef<HTMLDivElement>(null)
  const toc = blocks.flatMap((b, i) => (b.kind === 'heading' && b.level === 2 ? [{ index: i, text: b.text }] : []))
  const jump = (index: number) =>
    body.current?.querySelector(`[data-doc-heading="${index}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  const copy = () => copyText(markdown).then(
    () => toast('已複製 Markdown'),
    () => toast('複製失敗，請改用下載', { tone: 'bad' }),
  )

  return (
    <Card title="對外 API 說明文件" labelledBy="api-doc-title" hint={`站台網址：${baseUrl}`}
      right={<>
        <Button size="sm" onClick={copy}><Icon name="copy" size={13} />複製 Markdown</Button>
        <Button size="sm" onClick={() => saveText(DOC_FILENAME, markdown)}><Icon name="download" size={13} />下載 .md</Button>
      </>}>
      <nav className={d.toc} aria-label="文件目錄">
        {toc.map(t => (
          <button key={t.index} type="button" className={d.tocItem} onClick={() => jump(t.index)}>{t.text}</button>
        ))}
      </nav>
      <div className={d.doc} ref={body}>
        {blocks.map((b, i) => <BlockView key={i} block={b} index={i} />)}
      </div>
    </Card>
  )
}
