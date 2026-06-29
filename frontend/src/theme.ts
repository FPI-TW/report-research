import { createTheme, type MantineColorsTuple } from '@mantine/core'

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
  defaultRadius: 'md',
})
