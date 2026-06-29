import { theme } from './theme'

test('theme 用品牌金色作 primary，色票恰 10 階', () => {
  expect(theme.primaryColor).toBe('gold')
  expect(theme.colors?.gold).toHaveLength(10)
  expect(theme.colors?.gold?.[7]).toBe('#ae7415')
})
