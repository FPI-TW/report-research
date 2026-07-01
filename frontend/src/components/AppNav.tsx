import { Group, Text } from '@mantine/core'
import { Link, NavLink } from 'react-router'

const LINKS: ReadonlyArray<{ to: string; label: string }> = [
  { to: '/search', label: '搜尋' },
  { to: '/ask', label: '問答' },
  { to: '/monitor', label: '監控' },
]

const noUnderline = { textDecoration: 'none' } as const

export function AppNav() {
  return (
    <Group h="100%" px="md" justify="space-between" wrap="nowrap">
      <Link to="/search" style={noUnderline}>
        <Text fw={700} c="gold.7" size="lg">
          廷豐智能研報
        </Text>
      </Link>
      <Group component="nav" aria-label="主導覽" gap="lg" wrap="nowrap">
        {LINKS.map(({ to, label }) => (
          <NavLink key={to} to={to} style={noUnderline}>
            {({ isActive }) => (
              <Text fw={isActive ? 700 : 500} c={isActive ? 'gold.7' : 'dimmed'}>
                {label}
              </Text>
            )}
          </NavLink>
        ))}
      </Group>
    </Group>
  )
}
