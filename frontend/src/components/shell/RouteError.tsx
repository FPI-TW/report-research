import { useEffect } from 'react'
import { Link, useRouteError } from 'react-router'
import styles from './RouteError.module.css'

/**
 * 動態 import 失敗的訊息（各瀏覽器措辭不同）。
 *
 * 這是部署後最常見的一種「錯誤」而不是程式壞了：`make build-web` 換掉了帶 hash 的
 * chunk 檔名，仍開著舊分頁的人下一次切頁就會去抓一個已經不存在的檔案。重新載入即恢復。
 */
const CHUNK_LOAD_RE = /dynamically imported module|importing a module script failed|error loading dynamically imported/i

function messageOf(error: unknown): string {
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  return ''
}

interface Props {
  /** 連外殼（AppShell）都起不來時用：自己撐滿視窗，不假設外層有版面。 */
  standalone?: boolean
}

/**
 * 路由層的錯誤邊界（react-router 的 errorElement）。
 *
 * 先前全站只有 PDF 檢視器有邊界；任何一頁在 render 期間丟例外，React 會卸掉整棵樹，
 * 使用者看到的是沒有任何訊息、也沒有出口的白畫面。掛在外殼之內的那一層讓側欄留著，
 * 壞的只有內容區——切到別頁即自動清除（react-router 在 location 變動時重置錯誤）。
 */
export function RouteError({ standalone = false }: Props) {
  const error = useRouteError()
  const message = messageOf(error)
  const stale = CHUNK_LOAD_RE.test(message)

  useEffect(() => {
    // 邊界把例外吃掉了，這裡不留痕跡的話就沒有任何地方看得到它。
    console.error('[route] 頁面渲染失敗', error)
  }, [error])

  return (
    <div className={standalone ? `${styles.page} ${styles.standalone}` : styles.page}>
      <div className={styles.box} role="alert">
        <h2 className={styles.title}>{stale ? '站台已更新' : '這一頁發生問題'}</h2>
        <p className={styles.text}>
          {stale
            ? '站台剛部署了新版本，這個分頁還停在舊版。重新載入即可繼續。'
            : '頁面無法顯示。重新載入通常可以恢復；若持續發生，請把下方訊息回報給管理者。'}
        </p>
        <div className={styles.actions}>
          <button type="button" className={styles.btn} onClick={() => window.location.reload()}>
            重新載入
          </button>
          {!stale && <Link className={styles.link} to="/search">回到檢索</Link>}
        </div>
        {!stale && message && <code className={styles.detail}>{message}</code>}
      </div>
    </div>
  )
}
