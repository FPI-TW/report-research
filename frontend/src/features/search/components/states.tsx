// ── EmptyState ────────────────────────────────────────────────────────────────
interface EmptyStateProps {
  onReset: () => void
}

export function EmptyState({ onReset }: EmptyStateProps) {
  return (
    <div
      data-testid="empty-state"
      style={{ padding: '40px 20px', textAlign: 'center' }}
      role="status"
    >
      <div style={{ fontSize: 15, color: '#495057', marginBottom: 16 }}>找不到符合的研報</div>
      <button
        type="button"
        data-testid="empty-reset-btn"
        onClick={onReset}
        style={{
          padding: '6px 20px',
          border: '1px solid #dee2e6',
          borderRadius: 6,
          background: '#fff',
          color: '#228be6',
          cursor: 'pointer',
          fontSize: 14,
        }}
      >
        清除篩選
      </button>
    </div>
  )
}

// ── ErrorState ────────────────────────────────────────────────────────────────
interface ErrorStateProps {
  onRetry: () => void
}

export function ErrorState({ onRetry }: ErrorStateProps) {
  return (
    <div
      data-testid="error-state"
      style={{ padding: '40px 20px', textAlign: 'center' }}
      role="alert"
    >
      <div style={{ fontSize: 15, color: '#fa5252', marginBottom: 16 }}>載入失敗</div>
      <button
        type="button"
        data-testid="error-retry-btn"
        onClick={onRetry}
        style={{
          padding: '6px 20px',
          border: '1px solid #dee2e6',
          borderRadius: 6,
          background: '#fff',
          color: '#228be6',
          cursor: 'pointer',
          fontSize: 14,
        }}
      >
        重試
      </button>
    </div>
  )
}
