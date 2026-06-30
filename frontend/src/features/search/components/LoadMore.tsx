import React from 'react'

interface LoadMoreProps {
  hasMore: boolean
  loading: boolean
  onMore: () => void
}

export function LoadMore({ hasMore, loading, onMore }: LoadMoreProps) {
  if (!hasMore) return null

  return (
    <div
      data-testid="load-more-wrap"
      style={{ padding: '12px', textAlign: 'center' }}
    >
      <button
        type="button"
        data-testid="load-more-btn"
        disabled={loading}
        onClick={onMore}
        style={{
          padding: '6px 20px',
          border: '1px solid #dee2e6',
          borderRadius: 6,
          background: loading ? '#f8f9fa' : '#fff',
          color: loading ? '#868e96' : '#228be6',
          cursor: loading ? 'not-allowed' : 'pointer',
          fontSize: 14,
        }}
      >
        {loading ? '載入中…' : '載入更多'}
      </button>
    </div>
  )
}
