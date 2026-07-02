import { theme } from './theme'

test('theme 用品牌金色作 primary，色票恰 10 階', () => {
  expect(theme.primaryColor).toBe('gold')
  expect(theme.colors?.gold).toHaveLength(10)
  expect(theme.colors?.gold?.[7]).toBe('#ae7415')
})

test('headings 用襯線字型 token（var(--tf-serif)）', () => {
  expect(theme.headings?.fontFamily).toBe('var(--tf-serif)')
  expect(theme.headings?.fontWeight).toBe('700')
})

test('Card 預設帶新卡片語言（12px 圓角、邊框、淡陰影）', () => {
  expect(theme.components?.Card?.defaultProps).toMatchObject({
    radius: 12,
    withBorder: true,
    shadow: 'xs',
  })
})

test('陰影 xs 為設計系統的淡雙層卡片陰影', () => {
  expect(theme.shadows?.xs).toBe('0 1px 3px rgba(16, 24, 40, 0.06)')
})
