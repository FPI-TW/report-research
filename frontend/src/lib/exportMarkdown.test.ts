import { expect, test } from 'vitest'
import { answerMarkdown, briefMarkdown, citedNumbers } from './exportMarkdown'
import type { Source } from './askSchemas'

function src(n: number, over: Partial<Source> = {}): Source {
  return { n, report_id: `r${n}`, file_name: `f${n}.pdf`, title: `標題 ${n}`, market: 'TW', report_date: '2026-09-01', is_latest: false, ...over }
}

test('citedNumbers：依首次出現順序、去重', () => {
  expect(citedNumbers('甲 [2]，乙 [1][2]，丙 [10]。')).toEqual([2, 1, 10])
  expect(citedNumbers('沒有引用')).toEqual([])
})

test('只帶本文引用到的來源，依編號排序；沒引用到的不列', () => {
  const md = answerMarkdown({
    answer: '營收成長 [3]，毛利率持平 [1]。',
    sources: [src(1), src(2), src(3)],
    extSources: [],
  })
  expect(md).toBe(
    '營收成長 [3]，毛利率持平 [1]。\n\n**來源研報**\n[1] 標題 1（TW，2026-09-01）\n[3] 標題 3（TW，2026-09-01）',
  )
  expect(md).not.toContain('標題 2')
})

test('本文沒有任何引用標記時退回列全部來源，免得來源整個不見', () => {
  const md = answerMarkdown({ answer: '整體偏多。', sources: [src(1), src(2)], extSources: [] })
  expect(md).toContain('[1] 標題 1')
  expect(md).toContain('[2] 標題 2')
})

test('標題缺值回退檔名；日期缺值不留空括號', () => {
  const md = answerMarkdown({
    answer: '見 [1]。',
    sources: [src(1, { title: null, report_date: null, market: '' })],
    extSources: [],
  })
  expect(md).toContain('[1] f1.pdf')
  expect(md).not.toContain('（')
})

test('外部參考以連結列出；沒有來源時只有本文', () => {
  expect(answerMarkdown({
    answer: '答案',
    sources: [],
    extSources: [{ title: '央行新聞稿', url: 'https://example.org/a' }],
  })).toBe('答案\n\n**外部參考**\n- [央行新聞稿](https://example.org/a)')
  expect(answerMarkdown({ answer: '  只有本文  ', sources: [], extSources: [] })).toBe('只有本文')
})

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
