import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// Fluid Functionalism 的字級 utility（text-body、text-caption…）是字級不是顏色；tailwind-merge
// 預設把不認得的 text-<字> 當顏色，cn('text-body', 'text-muted-foreground') 會靜默丟掉字級。
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [
        'text-display',
        'text-title',
        'text-subtitle',
        'text-body',
        'text-caption',
        'text-micro',
      ],
    },
  },
})

/** shadcn／animate-ui／Fluid 元件用的 class 合併工具（clsx + tailwind-merge）。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
