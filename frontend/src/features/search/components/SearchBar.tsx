import React, { useEffect, useRef, useState } from 'react'
import { ActionIcon, TextInput } from '@mantine/core'

interface SearchBarProps {
  value: string
  onSubmit: (q: string) => void
  onClear: () => void
}

/**
 * 搜尋列元件（受控-ish，本地 state 用於打字；外部 value 改變時同步）。
 *
 * - 輸入後 debounce 450ms 再觸發 onSubmit（對齊 web/static/app/search.js）
 * - Enter → 立即觸發 onSubmit，取消 debounce
 * - 清除鈕 → 清空輸入、呼叫 onClear，取消 debounce
 * - 卸載時清除 debounce timer
 */
export function SearchBar({ value, onSubmit, onClear }: SearchBarProps) {
  const [text, setText] = useState(value)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const submitRef = useRef(onSubmit)

  const clearDebounce = () => {
    if (debounceRef.current != null) {
      clearTimeout(debounceRef.current)
      debounceRef.current = null
    }
  }

  useEffect(() => {
    submitRef.current = onSubmit
  }, [onSubmit])

  // 外部 value 改變時同步（例如 URL 狀態 reset）。
  useEffect(() => {
    clearDebounce()
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setText(value)
  }, [value])

  // 卸載時清除 debounce
  useEffect(() => {
    return () => {
      clearDebounce()
    }
  }, [])

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const next = e.target.value
    setText(next)
    clearDebounce()
    debounceRef.current = setTimeout(() => {
      submitRef.current(next)
    }, 450)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      clearDebounce()
      // 直接讀 DOM 值，避免 React state 更新非同步時取到舊值
      submitRef.current((e.target as HTMLInputElement).value)
    }
  }

  const handleClear = () => {
    clearDebounce()
    setText('')
    onClear()
  }

  return (
    <TextInput
      value={text}
      onChange={handleChange}
      onKeyDown={handleKeyDown}
      placeholder="搜尋研報..."
      aria-label="搜尋研報"
      rightSection={
        text ? (
          <ActionIcon
            variant="subtle"
            size="sm"
            aria-label="清除搜尋"
            onClick={handleClear}
          >
            ×
          </ActionIcon>
        ) : null
      }
    />
  )
}
