import { chartBlockSchema, kpiBlockSchema, type ChartBlock, type KpiBlock } from '../schemas'

export type ReportSegment =
  | { kind: 'md'; text: string }
  | { kind: 'kpi'; block: KpiBlock }
  | { kind: 'chart'; block: ChartBlock }

const FENCE_RE = /```(kpi|chart)\n([\s\S]*?)\n```/g

function pushMd(segments: ReportSegment[], text: string): void {
  if (text.trim() === '') return
  const last = segments[segments.length - 1]
  if (last?.kind === 'md') {
    last.text += text
  } else {
    segments.push({ kind: 'md', text })
  }
}

export function parseReportSegments(markdown: string): ReportSegment[] {
  const segments: ReportSegment[] = []
  let lastIndex = 0

  for (const match of markdown.matchAll(FENCE_RE)) {
    const [fenced, type, body] = match
    const start = match.index ?? 0
    pushMd(segments, markdown.slice(lastIndex, start))
    lastIndex = start + fenced.length

    const parsed = parseBlock(type, body)
    if (parsed) {
      segments.push(parsed)
    } else {
      pushMd(segments, fenced)
    }
  }
  pushMd(segments, markdown.slice(lastIndex))

  for (const segment of segments) {
    if (segment.kind === 'md') segment.text = segment.text.trim()
  }
  return segments
}

function parseBlock(type: string, body: string): ReportSegment | null {
  let json: unknown
  try {
    json = JSON.parse(body)
  } catch {
    return null
  }
  if (type === 'kpi') {
    const result = kpiBlockSchema.safeParse(json)
    return result.success ? { kind: 'kpi', block: result.data } : null
  }
  if (type === 'chart') {
    const result = chartBlockSchema.safeParse(json)
    return result.success ? { kind: 'chart', block: result.data } : null
  }
  return null
}
