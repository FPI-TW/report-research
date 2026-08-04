import type { Takeaway } from '../../lib/readingSchemas'
import type { JumpResult } from './pdf/PdfViewer'
import styles from './TakeawayList.module.css'

interface Props {
  takeaways: Takeaway[]
  /**
   * 整區能不能互動。false＝這篇沒有可搜尋的 PDF（.docx／無檔／引擎已降級成內建檢視）。
   * **整批切換，不逐條標灰**：逐條標灰會被讀成「這條資料有問題」，其實是引擎層的事。
   */
  canJump?: boolean
  /** 正在尋找中的那一條（點下去到結果回來之間） */
  pendingOrdinal?: number | null
  /** 最近一次尋找的結果 */
  result?: (JumpResult & { ordinal: number }) | null
  onJump?: (t: Takeaway) => void
}

/**
 * 重點摘錄：claim ＋ 逐字引文。takeaways 為空時由呼叫端整區不渲染。
 *
 * **動詞刻意是「尋找」不是「跳轉」。** 點下去會拿這條引文去 PDF 裡跑一次真的搜尋
 * （全語料 3,407 條實測：原句命中 95.3%，加上關鍵字階梯 98.6–99.1%）。
 * 找不到是**一個可呈現的結果**，不是壞掉 —— 讀者會看到「原文中找不到這段文字」，
 * 知道系統做了什麼、結果是什麼。這與 2026-08-03 移除的那個舊互動不同：舊的寫著
 * 「跳至原文位置」卻根本沒有落點，按下去毫無反應、零訊息。
 *
 * 三種狀態都看得見（pending／頁碼／找不到），**沒有任何路徑通往「什麼都沒發生」**。
 *
 * 不可跳時維持非互動的 `<div>`，**不要用 disabled button**：disabled 預設不可聚焦，
 * 螢幕閱讀器會整條跳過，讀者連這條摘錄存在都不知道。
 */
export function TakeawayList({ takeaways, canJump, pendingOrdinal, result, onJump }: Props) {
  return (
    <div className={styles.list}>
      {/* 結果文字長在**被點的那顆按鈕內部**，而焦點就停在那顆按鈕上 ——
          焦點元素內部的內容變動不會被螢幕閱讀器播報，所以另給一個 live region。
          放在左欄（跟著焦點所在的那一欄），不放進檢視器。 */}
      {/* key 帶 nonce：兩條摘錄命中同一頁時字串完全相同，live region 內容沒變就
          不會重播，第二次點擊對螢幕閱讀器等於零回饋。換 key 讓節點重建才會再播一次。 */}
      <span key={result?.nonce ?? 'idle'} className={styles.srOnly} role="status">
        {result?.ok
          ? `已在第 ${result.page} 頁找到${(result.total ?? 1) > 1 ? `，此句全篇出現 ${result.total} 次` : ''}`
          : result
            ? '原文中找不到這段文字'
            : ''}
      </span>
      {takeaways.map(t => {
        const body = (
          <div className={styles.in}>
            <span className={styles.n}>{t.ordinal}</span>
            <span className={styles.x}>
              {t.claim}
              {t.quote && <span className={styles.q}>{t.quote}</span>}
              {pendingOrdinal === t.ordinal && <span className={styles.state}>尋找中…</span>}
              {/* 成功時**不顯示任何文字**：PDF 真的捲過去並高亮，那本身就是回饋，
                  再標一次頁碼是重複。唯一的例外是多重命中 —— 約 6-7% 的引文在同一份
                  PDF 裡不只出現一次（其中約 1/3 還在同一頁），跳到的是第一處，未必是
                  摘錄真正引用的那一處。不講的話，落錯位置看起來會跟成功一模一樣。
                  （看不見畫面的讀者拿不到「捲過去了」這個回饋，所以頁碼仍保留在
                    下方的 live region 裡。） */}
              {result?.ordinal === t.ordinal && result.ok && (result.total ?? 1) > 1 && (
                <span className={styles.state}>共 {result.total} 處</span>
              )}
              {result?.ordinal === t.ordinal && !result.ok && (
                <span className={styles.miss}>原文中找不到這段文字</span>
              )}
            </span>
          </div>
        )

        // 沒有引文就沒有可搜尋的東西——那類條目一律非互動（claim 是 LLM 的轉述，
        // 拿它去搜 PDF 幾乎必然落空）。
        if (!canJump || !t.quote) {
          return (
            <div key={t.ordinal} className={styles.tk}>
              {body}
            </div>
          )
        }

        return (
          // **這裡不可以掛 aria-label**：它會**覆蓋**由後代內容算出的可及名稱，而按鈕在
          // 可及性樹上是葉節點，讀者無法「進去」讀內容。掛上去等於把 claim、逐字引文、
          // 尋找中、共 N 處、找不到全部對螢幕閱讀器關掉 —— 而 PDF 內文本來就沒有文字節點
          // （SelectionLayer 只畫色塊），左欄摘錄是視障讀者唯一拿得到研報內容的地方。
          // 動作說明改成 sr-only 節點附加在內容之後：可及名稱＝內容 ＋ 動作。
          <button
            key={t.ordinal}
            type="button"
            className={`${styles.tk} ${styles.tkOn}`}
            onClick={() => onJump?.(t)}
          >
            {body}
            <span className={styles.srOnly}>：在原文中尋找這段引文</span>
          </button>
        )
      })}
    </div>
  )
}
