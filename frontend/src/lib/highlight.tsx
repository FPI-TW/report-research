import { Fragment } from 'react'
import type { ReactNode } from 'react'

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * 把 text 中命中 terms 的片段包成 <mark>，其餘為純文字節點。
 * 用單一 capture group split：偶數索引為原文、奇數索引為命中片段。
 * 回傳 React 節點陣列——絕不注入 HTML，天生 XSS 安全。
 */
export function highlight(text: string, terms: string[]): ReactNode[] {
  if (!terms.length || !text) return [text]
  const re = new RegExp('(' + terms.map(escapeRegExp).join('|') + ')', 'gi')
  return text.split(re).map((part, i) =>
    i % 2 === 1
      ? <mark key={i}>{part}</mark>
      : <Fragment key={i}>{part}</Fragment>,
  )
}
