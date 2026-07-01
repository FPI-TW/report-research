export type ReportStage = 'retrieving' | 'searching_web' | 'writing' | 'rendering'

export interface ReportDone {
  report_id: string
  title: string
  download_url: string
  thinking_ms?: number
}

export type ReportEvent =
  | { event: 'status'; data: { stage: ReportStage } }
  | { event: 'sources'; data: unknown }
  | { event: 'token'; data: string }
  | { event: 'done'; data: ReportDone }
  | { event: 'error'; data: { detail?: string } }
