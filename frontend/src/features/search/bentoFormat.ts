export function monthOf(date: string | null): string {
  if (!date) return ''
  const [year, month] = date.slice(0, 10).split('-')
  return year && month ? `${year} 年 ${Number(month)} 月` : ''
}
