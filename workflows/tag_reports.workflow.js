export const meta = {
  name: 'tag-reports',
  description: '分批 fan-out：每個 agent 讀一個 worklist 批次檔，逐檔讀 PDF 判定市場標籤，寫 data/tags/<hash>.json',
  phases: [{ title: 'Tag', detail: '平行標註各批研報的市場標籤' }],
}

const projectRoot = '/mnt/c/Users/User/Desktop/Project/report-mark'
const tagsDir = 'data/tags'
const batchFiles = [
  'data/worklist_batch0.json',
  'data/worklist_batch1.json',
  'data/worklist_batch2.json',
  'data/worklist_batch3.json',
  'data/worklist_batch4.json',
]

const MARKETS = ['TW', 'US', 'HK', 'CN', 'FX', 'WTX', 'MACRO', 'GLOBAL', 'CRYPTO']
const INSTRUMENT_TYPES = ['equity', 'index', 'futures', 'options', 'etf', 'bond', 'fx', 'commodity', 'crypto']

const TAG_RULES = `你是研究報告分類助手。標籤對齊 findb 代碼與固定詞表。

工作流程：
1. 用 Read 工具讀取指派給你的 worklist 批次檔（JSON 陣列，每筆有 file_hash / file_path / file_name）。
2. 對陣列中「每一個」檔案，用 Read 工具讀取該 file_path 指向的 PDF/Word 內容。
3. 判斷該報告涵蓋的「主要市場」，market 從下列 findb 代碼選一個（大寫）：
   ${MARKETS.join('、')}
   - TW：台股，台灣上市櫃個股/產業（4碼代碼如1102、2317；本土券商個股報告）
   - US：美股，美國個股/市場（多為英文外資報告、webcast/flyer法說）
   - HK：港股，香港掛牌個股/市場
   - CN：陸股，中國A股、陸股策略
   - FX：外匯、匯率
   - WTX：台指期，期貨、選擇權、部位限制（如台指期盤後報）
   - MACRO：總經、利率、跨市場宏觀策略；債券/固定收益（債券週報、殖利率）也歸 MACRO
   - GLOBAL：跨市場資產配置、全球策略；原物料/商品/能源/金屬也歸 GLOBAL
   - CRYPTO：加密貨幣
   非研究檔（人事異動、行事曆、組織架構、部位限制公告、webcast flyer、發票帳單等）is_research 設 false。
4. 判斷 instrument_types：報告「實際提及」的金融商品類型，從下列詞表選 0 到多個（小寫，可複選）：
   ${INSTRUMENT_TYPES.join('、')}
   （equity股票/個股、index指數/大盤、futures期貨、options選擇權、etf ETF、bond債券/固收、
   fx外匯、commodity原物料、crypto加密貨幣；詞表外不要輸出，無明確商品給 []。）
5. 判斷兩個獨立布林（與 instrument_types 語意不同，看「是否有參考價值」）：
   - relates_stock：對「選股/個股交易」是否有參考價值（個股/產業報告通常 true）。
   - relates_futures：對「期貨/指數部位」是否有參考價值（台指期、大盤策略、總經部位通常 true，
     即使報告本身不含 futures 商品也可能 true）。
6. 判斷具體標的：
   - stock_targets：報告聚焦/評等/重點討論的個股 4 碼代碼清單（依重要性排序，最多 8 檔）；
     個股報告放該標的 1 檔，週報/產業只列重點討論者，純總經/匯率給 []；
     只輸出能確定 4 碼代碼者（別把日期/頁碼當代碼）。
   - futures_targets：報告涉及的期貨商品，從固定小詞表選 0 到多個（詞表外不輸出）：
     台指期、小型台指、電子期、金融期、個股期貨、其他；台指期盤後報→["台指期"]。
7. 用 Write 工具把結果寫到 ${tagsDir}/<file_hash>.json，內容為單一 JSON 物件：
   {"market":"<上列代碼其一>","is_research":true/false,"confidence":0~1,"instrument_types":["equity","futures"],"relates_stock":true,"relates_futures":false,"stock_targets":["2330"],"futures_targets":["台指期"]}
   非研究檔 is_research=false（其餘欄位仍盡量填，instrument_types / stock_targets / futures_targets 可為 []）。

注意：務必處理批次檔中的「所有」檔案，每個檔案各寫一個 tag JSON。處理完回報已寫出的檔案數量。`

phase('Tag')

const results = await parallel(
  batchFiles.map((bf, bi) => () => {
    const prompt = `${TAG_RULES}\n\n你的批次檔（請先 Read 它）：${projectRoot}/${bf}\n輸出目錄：${projectRoot}/${tagsDir}/`
    return agent(prompt, { label: `tag:batch${bi}`, phase: 'Tag' })
  })
)

log(`tagging done: ${batchFiles.length} batches`)
return { batches: batchFiles.length, results }
