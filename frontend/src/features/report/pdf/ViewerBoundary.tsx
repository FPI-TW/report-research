import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** 引擎不可用時改渲染的內容（本站是瀏覽器內建 iframe） */
  fallback: ReactNode
  onError?: (error: Error) => void
}

interface State {
  failed: boolean
}

/**
 * PDF 引擎的降級邊界。
 *
 * WASM 起不來的成因很多且都在我們控制之外：企業代理擋掉 .wasm、記憶體不足、
 * 舊瀏覽器不支援、資產部署漏掉。**這些情況下讀者仍然必須讀得到研報**，
 * 所以一律退回今天已經在跑的瀏覽器內建檢視。
 *
 * 刻意用類別元件：React 至今沒有 hook 版的 error boundary。
 * 刻意不提供「重試」：WASM 載入失敗幾乎都是環境層級、重試一次不會變好，
 * 給一顆按了照樣失敗的按鈕只是把挫折延後。
 */
export class ViewerBoundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 降級是靜默的（讀者只會發現版面變成內建檢視），故一定要留下可觀測的痕跡，
    // 否則「為什麼大家的檢視器都長得不一樣」永遠查不出來。
    console.error('[pdf] 引擎不可用，已退回內建檢視', error, info.componentStack)
    this.props.onError?.(error)
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}
