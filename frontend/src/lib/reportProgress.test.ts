import { expect, test } from 'vitest'
import { reportProgress } from './reportProgress'

test('各階段映射里程碑 % 與文案', () => {
  expect(reportProgress('retrieving')).toEqual({ pct: 20, text: '深度檢索研報中…' })
  expect(reportProgress('searching_web')).toEqual({ pct: 40, text: '搜尋網路補充…' })
  expect(reportProgress('writing')).toEqual({ pct: 50, text: '撰寫研報中…' })
  expect(reportProgress('rendering')).toEqual({ pct: 90, text: '排版 PDF 中…' })
})
