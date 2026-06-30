import { Select } from '@mantine/core'

type GroupByValue = 'month' | 'market'

interface GroupBySelectProps {
  value: GroupByValue
  onChange: (g: GroupByValue) => void
}

const GROUP_DATA = [
  { value: 'month', label: '依日期' },
  { value: 'market', label: '依市場' },
]

export function GroupBySelect({ value, onChange }: GroupBySelectProps) {
  return (
    <Select
      label="分組依據"
      data={GROUP_DATA}
      value={value}
      onChange={(v) => {
        if (v !== null) onChange(v as GroupByValue)
      }}
      allowDeselect={false}
    />
  )
}
