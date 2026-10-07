import type { Progress } from '../../../../lib/progressSchema'

/** 管線分頁測試共用的 /api/progress 回應：一切正常、沒有批次在跑。各測試用 override 改需要的區塊。 */
export function progressFixture(over: Partial<Progress> = {}): Progress {
  return {
    ts: '14:32:05',
    db: {
      reports: 16220,
      chunks: 402318,
      markets: [{ market: 'US', count: 3910 }, { market: 'TW', count: 7342 }, { market: null, count: 263 }],
      sources: [
        { source: 'kgi', display: '凱基', count: 2214, latest: '2026-10-07' },
        { source: 'goldman_sachs', display: '高盛', count: 1322, latest: '2026-10-06' },
        { source: null, display: null, count: 327, latest: '2026-10-07' },
      ],
    },
    summary: { done: 16108, total: 16220, remaining: 112, pct: 99.3 },
    tagging: null,
    ingest: null,
    pipelines: {
      web: true, ingest: false, sync_import: false, tag: false, summaries: false,
      titles: false, takeaways: false, signals: false, backfill: false,
    },
    orchestrator: null,
    takeaway: { done: 1388, total: 1412, remaining: 24, pct: 98.3, latest: '2026-10-07' },
    signal: { done: 402, total: 1412, remaining: 1010, pct: 28.5, latest: '2026-10-06' },
    evaluation: {
      min_score: 0.9,
      qa: {
        total: 1380, checked: 412, degraded: 0, below_min: 3, avg_score: 0.947, latest: '2026-10-07 13:58',
        judge_model: 'deepseek-flash', judge_since: '2026-09-26', other_judge_checked: 0, judge_checked: 324, avg_n: 318,
      },
    },
    sync: {
      raw: '[2026-10-07 12:41:17] === sync done ===', timestamp: '2026-10-07 12:41:17', status: 'done', label: '同步已完成',
    },
    unit_failures: {
      count_24h: 0, count_7d: 1, latest: '2026-10-03T21:00:04+08:00',
      recent: [{ ts: '2026-10-03T21:00:04+08:00', unit: 'report-mark-freshness.service', stage: 'signal', rc: null }],
    },
    extraction: {
      target_version: 'v4', needs_review: 128, pages_failed: 9, log_latest: '2026-10-07 04:52',
      stopped_at: { ingested: 16220, not_research: 1203, skip_admin: 188, scanned: 61, extract_error: 0 },
      versions: [{ version: 'v4', count: 9928 }, { version: '(unknown)', count: 6292 }],
      backfill: { done: 9928, total: 16220, remaining: 6292, pct: 61.2, latest: '2026-10-07 04:52' },
    },
    ...over,
  }
}
