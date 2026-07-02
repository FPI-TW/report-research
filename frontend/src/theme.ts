import { Card, createTheme, Paper, type MantineColorsTuple } from '@mantine/core'

// 由品牌金 #ae7415（tokens.css --brand）衍生的 10 階色票；index 7 = 品牌主色。
const gold: MantineColorsTuple = [
  '#fbf3e3',
  '#f3e4c6',
  '#e7c98c',
  '#dbaf52',
  '#d09a26',
  '#c98e10',
  '#c58808',
  '#ae7415', // 品牌主色（--brand）
  '#9c6710',
  '#8a5a0f', // --brand-strong
]

export const theme = createTheme({
  colors: { gold },
  primaryColor: 'gold',
  primaryShade: { light: 7, dark: 6 },
  fontFamily:
    '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif',
  // 襯線編輯感：標題層級一律 Noto Serif TC（token 定義在 styles/tokens.css）
  headings: { fontFamily: 'var(--tf-serif)', fontWeight: '700' },
  defaultRadius: 'md',
  // 設計系統淡卡片陰影（覆寫 xs 一階，其餘沿用 Mantine 預設）
  shadows: { xs: '0 1px 3px rgba(16, 24, 40, 0.06)' },
  components: {
    Card: Card.extend({
      defaultProps: { radius: 12, withBorder: true, shadow: 'xs' },
    }),
    Paper: Paper.extend({
      defaultProps: { radius: 12 },
    }),
  },
})
