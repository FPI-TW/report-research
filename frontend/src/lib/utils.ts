import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** shadcn/animate-ui 元件用的 class 合併工具（clsx + tailwind-merge）。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
