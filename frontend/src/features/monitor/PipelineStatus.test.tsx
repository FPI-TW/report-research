import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PIPELINE_ROWS, PipelineStatus } from './PipelineStatus'

test('5 列名稱 + 狀態徽章對應布林', () => {
  render(<PipelineStatus pipelines={{ web: true, ingest: false, tag: true, summaries: false, signals: true }} />)
  for (const n of ['Web 服務', '報告導入', '語意標註', '摘要生成', '雷達訊號']) {
    expect(screen.getByText(n)).toBeInTheDocument()
  }
  expect(screen.getAllByText('執行中')).toHaveLength(3)   // web + tag + signals
  expect(screen.getAllByText('已停止')).toHaveLength(2)   // ingest + summaries
})

test('列順序固定為 web/ingest/tag/summaries/signals', () => {
  const { container } = render(
    <PipelineStatus pipelines={{ web: true, ingest: true, tag: true, summaries: true, signals: true }} />,
  )
  const names = [...container.querySelectorAll('[class*="pipeName"]')].map(el => el.textContent)
  expect(names).toEqual(['Web 服務', '報告導入', '語意標註', '摘要生成', '雷達訊號'])
})

// 滾動部署：後端還沒上線時 `signals` 是 undefined。該列仍要在（顯示已停止），
// 不可因為缺鍵而消失或渲染成空白徽章——否則「看不到那一列」與「批次沒在跑」
// 兩種情況在畫面上無法區分，正是這次要修掉的那個歧義。
test('後端未回 signals 時該列仍在並顯示已停止', () => {
  render(<PipelineStatus pipelines={{ web: true, ingest: false, tag: false, summaries: false }} />)
  expect(screen.getByText('雷達訊號')).toBeInTheDocument()
  expect(screen.getAllByText('已停止')).toHaveLength(4)   // ingest + tag + summaries + signals
})

test('PIPELINE_ROWS 是頁首分母的單一來源', () => {
  expect(PIPELINE_ROWS.map(r => r.key)).toEqual(['web', 'ingest', 'tag', 'summaries', 'signals'])
})
