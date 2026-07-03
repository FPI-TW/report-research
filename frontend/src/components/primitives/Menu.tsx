import type { ReactNode } from 'react'

export function MenuItem({ children, onClick }: { children: ReactNode; onClick?: () => void }) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      style={{
        width: '100%', textAlign: 'left', border: 'none', background: 'none',
        fontSize: 13, color: 'var(--tf-text-2)', padding: '8px 10px',
        borderRadius: 8, cursor: 'pointer', fontFamily: 'inherit',
      }}
    >
      {children}
    </button>
  )
}
