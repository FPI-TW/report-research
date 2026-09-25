import { expect, test } from 'vitest'
import { briefMarkdown } from './exportMarkdown'

test('簡報：標題、本文、來源清單與未列入的差額', () => {
  const md = briefMarkdown({
    brief_date: '2026-09-21', window_start: 'a', window_end: 'b', markdown: '## 重點\n- 半導體偏多\n',
    report_count: 5, signal_count: 2, created_at: 'c',
    reports: [
      { report_id: 'r1', file_hash: 'h', file_name: 'a.pdf', title: '台積電展望', source_display: '凱基', report_date: '2026-09-20' },
      { report_id: 'r2', file_hash: 'h', file_name: 'b.pdf', title: null, source: 'yuanta' },
    ],
  })
  expect(md).toBe(
    '# 每日研報簡報 2026-09-21\n\n## 重點\n- 半導體偏多\n\n## 本期來源研報\n'
    + '- 台積電展望（凱基，2026-09-20）\n- b.pdf（yuanta）\n- 另有 3 篇未列入本期彙整',
  )
})
