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
    {/* reducedMotion="user" 自動停用所有 motion 子元件的位移/scale/layout 動畫（保留 opacity），
        故 hover/press 的 scale 於減少動態時免逐一加 guard；transition 預設對齊 --tf-dur-3/--tf-ease-out。 */}
    <MotionConfig reducedMotion="user" transition={tfTransition}>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </MotionConfig>
  </StrictMode>,
)
