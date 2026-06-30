import { Fragment } from 'react'
import { highlightSegments } from '../lib/highlight'

interface HighlightProps {
  text: string
  terms: string[]
}

export function Highlight({ text, terms }: HighlightProps) {
  const segs = highlightSegments(text, terms)
  return (
    <>
      {segs.map((s, i) =>
        s.mark ? <mark key={i}>{s.text}</mark> : <Fragment key={i}>{s.text}</Fragment>,
      )}
    </>
  )
}
