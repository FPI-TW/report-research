// 對外 API 說明的唯一真相在 repo 的 docs/EXTERNAL_API.md（`@docs` 別名見 vite.config.ts 與 vitest.config.ts）。
// 建置時把文字打包進來：文件與後端一起改、一起部署，後台看到的就是這一版程式對應的文件。
import markdown from '@docs/EXTERNAL_API.md?raw'

export const EXTERNAL_API_MD: string = markdown
