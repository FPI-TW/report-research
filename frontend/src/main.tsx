import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MotionConfig } from 'motion/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { tfTransition } from './lib/motionTokens'
import './styles/tokens.css'
import './styles/view-transitions.css'
import App from './App'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* reducedMotion="never"：不做全域一刀切停用，改由各元件自行以 useReducedMotion() 決定。
        如此「功能性微互動」（按壓/hover/分段滑動 thumb/清單退場）即使系統開了減少動態仍照常播（皆為小幅、非前庭誘發的位移）；
        而大幅進場波浪、裝飾迴圈、Modal/Popover/抽屜/數字補間等已各自 guard，仍尊重減少動態偏好。
        transition 預設對齊 --tf-dur-3/--tf-ease-out。 */}
    <MotionConfig reducedMotion="never" transition={tfTransition}>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </MotionConfig>
  </StrictMode>,
)
