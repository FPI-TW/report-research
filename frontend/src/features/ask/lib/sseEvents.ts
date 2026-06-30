export interface SourceItem {
  n: number
  report_id: string
  file_name: string
  market: string | null
  report_date: string | null
  is_latest: boolean
}

export interface ExtSource {
  title?: string | null
  url: string
}

export interface StatusPayload {
  stage: string
  count?: number
  thinking_ms?: number
}

export interface DonePayload {
  cited?: string[]
  qa_id?: string | null
  conversation_id?: string | null
  thinking_ms?: number | null
  offer_report?: boolean
  report_title?: string | null
}

export type AskEvent =
  | { event: 'status'; data: StatusPayload }
  | { event: 'sources'; data: SourceItem[] }
  | { event: 'ext_sources'; data: ExtSource[] }
  | { event: 'token'; data: string }
  | { event: 'notice'; data: string }
  | { event: 'done'; data: DonePayload }
  | { event: 'error'; data: { detail?: string } }
