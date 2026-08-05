import styles from './MonitorPage.module.css'
import { fmtInt } from './rate'
import type { SourceCount } from './progressSchema'

/** 檔名認不出券商的那一列（後端 source 為 NULL）。 */
const UNIDENTIFIED = '未辨識'

/**
 * NULL source 那一列的 React key。
 *
 * 需要哨兵是因為 key 不能是 null，而直接寫 `'unidentified'` 理論上會與某個真的叫
 * unidentified 的 source 代碼相撞。雙底線這種寫法在 source 代碼裡不可能出現
 * （後端的代碼是 kgi／goldman_sachs 這類正規化過的字串）。
 *
 * **不要用 `'\0unidentified'` 這種不可見字元當哨兵**（本檔原本如此）：它不會造成任何
 * 執行期錯誤，所以 tsc、vitest、build 全部照過，但 git 只要在檔案前 8000 bytes 看到
 * 一個 NUL 就會把整份原始碼判定為 binary —— PR 上看不到任何 diff、無法審查，
 * 而且那個字元在編輯器裡是隱形的，下一個改到這行的人不會知道自己刪了什麼。
 */
const NULL_SOURCE_KEY = '__null_source__'

/**
 * 券商分佈：語料裡有哪些券商的研報、各多少篇、最新一篇是哪天。
 *
 * 與市場分佈的差別不只是換一個維度——**這頁是「導入」監控，所以 latest 才是重點**：
 * 光看篇數看不出某家券商是不是早就停止供稿（實測 masterlink 3,814 篇、最新一篇停在
 * 一年前）。刻意**不**對過舊的日期上紅色或警示：停供稿有可能是合約到期這種正常狀態，
 * 分不出來的東西一旦上警示就會永遠亮著，而本專案已經有「永遠紅的東西會被停用」的教訓。
 *
 * 數字不用 TweenNumber：市場分佈只有個位數列，這裡實測 31 列，31 個 count-up 同時
 * 跑只是噪音，而這些值本來就是幾乎不動的語料累計量。
 */
export function BrokerDistribution({ sources }: { sources?: SourceCount[] }) {
  // 後端已排序，這裡仍排一次：排序是本元件的視覺契約，不該靠上游的 ORDER BY 維持。
  const rows = [...(sources ?? [])].sort((a, b) => b.count - a.count)
  const total = rows.reduce((s, r) => s + r.count, 0)
  const max = Math.max(1, ...rows.map(r => r.count))
  const namedCount = rows.filter(r => r.source !== null).length
  const unidentified = rows.find(r => r.source === null)?.count ?? 0

  return (
    <div className={`${styles.card} ${styles.panel} ${styles.marginTop}`}>
      <div className={styles.ptitle}>券商分佈</div>
      {/* 三態要分得開：undefined＝舊後端沒這個欄位（降級但不消失，比照排程健康卡）、
          空陣列＝真的沒有研報、有列＝正常。全部塌成「—」的話，滾動部署期間的
          缺欄位會長得跟空語料一模一樣。 */}
      {sources === undefined ? (
        <div className={styles.pidle}>此版後端未提供券商統計</div>
      ) : rows.length === 0 ? (
        <div className={styles.pidle}>—</div>
      ) : (
        <>
          <div className={styles.brkSum}>
            {`共 ${namedCount} 家券商 · ${fmtInt(total)} 篇`}
            {unidentified > 0 ? `（其中未辨識 ${fmtInt(unidentified)} 篇）` : ''}
          </div>
          <div className={styles.brkList}>
            {rows.map(r => {
              const label = r.display || r.source || UNIDENTIFIED
              return (
                <div key={r.source ?? NULL_SOURCE_KEY} className={styles.brkRow}>
                  {/* title 給原始代碼：中文名對不上時要查得到自己在看哪一個 source */}
                  <span className={styles.brkLabel} title={r.source ?? undefined}>{label}</span>
                  <div className={styles.brkBar}>
                    <div
                      className={styles.brkFill}
                      style={{ width: `${((r.count / max) * 100).toFixed(1)}%` }}
                    />
                  </div>
                  <span className={styles.brkCount}>{fmtInt(r.count)}</span>
                  <span className={styles.brkDate}>{r.latest ?? '—'}</span>
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
