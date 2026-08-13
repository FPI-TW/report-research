import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PipelineStatus } from './PipelineStatus'
import { DERIVED_LABEL, PIPELINE_ROWS } from './pipelineMeta'

test('8 列名稱 + 狀態徽章對應布林', () => {
  render(
    <PipelineStatus
      pipelines={{
        web: true, ingest: false, sync_import: true, tag: true,
        summaries: false, titles: true, takeaways: true, signals: true,
      }}
    />,
  )
  for (const n of ['Web 服務', '報告導入', '增量匯入', '語意標註', '摘要生成', '顯示標題', '重點摘錄', '觀點訊號']) {
    expect(screen.getByText(n)).toBeInTheDocument()
  }
  // web + sync_import + tag + titles + takeaways + signals
  expect(screen.getAllByText('執行中')).toHaveLength(6)
  expect(screen.getAllByText('已停止')).toHaveLength(2)   // ingest + summaries
})

test('列順序固定為 web/ingest/sync_import/tag/summaries/titles/takeaways/signals', () => {
  const { container } = render(
    <PipelineStatus
      pipelines={{
        web: true, ingest: true, sync_import: true, tag: true,
        summaries: true, titles: true, takeaways: true, signals: true,
      }}
    />,
  )
  const names = [...container.querySelectorAll('[class*="pipeName"]')].map(el => el.textContent)
  expect(names).toEqual([
    'Web 服務', '報告導入', '增量匯入', '語意標註', '摘要生成', '顯示標題',
    '重點摘錄', '觀點訊號',
  ])
})

// 滾動部署：後端還沒上線時 takeaways／signals／sync_import 是 undefined。三列仍要在
//（顯示已停止），不可因為缺鍵而消失或渲染成空白徽章——否則「看不到那一列」與
// 「批次沒在跑」兩種情況在畫面上無法區分，正是這幾列要修掉的那個歧義。
test('後端未回 takeaways／signals／sync_import／titles 時四列仍在並顯示已停止', () => {
  render(<PipelineStatus pipelines={{ web: true, ingest: false, tag: false, summaries: false }} />)
  expect(screen.getByText('重點摘錄')).toBeInTheDocument()
  expect(screen.getByText('觀點訊號')).toBeInTheDocument()
  expect(screen.getByText('增量匯入')).toBeInTheDocument()
  expect(screen.getByText('顯示標題')).toBeInTheDocument()
  // ingest+sync_import+tag+summaries+titles+takeaways+signals
  expect(screen.getAllByText('已停止')).toHaveLength(7)
})

test('PIPELINE_ROWS 是頁首分母的單一來源', () => {
  expect(PIPELINE_ROWS.map(r => r.key)).toEqual([
    'web', 'ingest', 'sync_import', 'tag', 'summaries', 'titles', 'takeaways', 'signals',
  ])
})

// 管線列的名稱必須真的取自 DERIVED_LABEL，而不是各自寫死同樣的字。前者改一處
// 兩邊一起動，後者會漂（「雷達訊號」vs「觀點訊號」就是這樣來的）。
// 注意這裡刻意不寫成「兩邊字串相等」——那種斷言在兩邊各自硬編時也會過，
// 證明不了單一來源，只證明「現在剛好一樣」。
test('摘錄／訊號兩列的名稱取自 DERIVED_LABEL', () => {
  const byKey = Object.fromEntries(PIPELINE_ROWS.map(r => [r.key, r.name]))
  expect(byKey.takeaways).toBe(DERIVED_LABEL.takeaways)
  expect(byKey.signals).toBe(DERIVED_LABEL.signals)
})
