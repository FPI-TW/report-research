/**
 * 研報顯示標題的單一事實來源。
 *
 * 檔名多半是券商流水號（624726992507895929_260728_gs_umt.pdf），對讀者沒有意義，
 * 故一律顯示報告內部標題（scripts/generate_titles.py 產出，一律繁體中文）。
 *
 * `title` 是漸進補上的：任何時點都會有一部分報告還沒有標題（批次還沒跑到、或內文
 * 抽字損毀而刻意不猜），**缺值是常態不是錯誤** → 回退檔名，畫面照常。
 */
export function displayTitle(
  row: { title?: string | null; file_name?: string | null } | null | undefined,
  fallback = '報告',
): string {
  const title = row?.title?.trim()
  if (title) return title
  const fileName = row?.file_name?.trim()
  return fileName || fallback
}
