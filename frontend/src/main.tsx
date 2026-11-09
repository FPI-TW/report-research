import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MotionConfig } from 'motion/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { applyAdminThemeEarly } from './lib/adminTheme'
import { loadBranding } from './lib/branding'
import { loadMarkets } from './lib/marketsConfig'
import { tfTransition } from './lib/motionTokens'
import { createQueryClient } from './lib/queryClient'
import { LocaleBoundary, initI18n } from './i18n'
// 自架字型（不連 Google Fonts）：Inter 帶 opsz 軸；JetBrains Mono 只給標的代號。
import '@fontsource-variable/inter/opsz.css'
import '@fontsource-variable/jetbrains-mono/index.css'
import './styles/tokens.css'
import './styles/tailwind.css'
import App from './App'

const queryClient = createQueryClient()

// 直接開管理後台時先把主題寫上 <html>，外殼的 chunk 下載完之前不會先閃一次亮底。
applyAdminThemeEarly()

// 品牌（免登入）與市場子集（profile）：不等它們回來就開始畫，抓到之後才換上機構名、logo 與市場選單；
// 抓不到時留在中性預設與全部標準市場（lib/branding.ts、lib/marketsConfig.ts）。
// 例外是介面語系：使用者沒選過語言時要等 profile 的預設語系（最多 1.5 秒），字典載好才開始畫（src/i18n）。
const branding = loadBranding()
void loadMarkets()

void initI18n(branding.then(b => ({ defaultLocale: b.defaultLocale ?? undefined, availableLocales: b.availableLocales ?? undefined })))
  .then(render)

function render() {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      {/* reducedMotion="never"：不做全域一刀切停用，改由各元件自行以 useReducedMotion() 決定。
          如此「功能性微互動」（按壓/hover/分段滑動 thumb/清單退場）即使系統開了減少動態仍照常播（皆為小幅、非前庭誘發的位移）；
          而大幅進場波浪、裝飾迴圈、Modal/Popover/抽屜/數字補間等已各自 guard，仍尊重減少動態偏好。
          transition 預設對齊 --tf-dur-3/--tf-ease-out。 */}
      <MotionConfig reducedMotion="never" transition={tfTransition}>
        <QueryClientProvider client={queryClient}>
          {/* 切換語系時重掛畫面樹（查詢快取在外面，不會丟）。 */}
          <LocaleBoundary>
            <App />
          </LocaleBoundary>
        </QueryClientProvider>
      </MotionConfig>
    </StrictMode>,
  )
}
