import { useBranding } from '../lib/branding'

/** 品牌標記：profile 的 logo（`GET /api/branding`，亮暗兩版）；讀不到品牌或 profile 沒設 logo 時畫中性的文件圖示。
 *  alt 預設為產品名；置於已有無障礙標籤的按鈕內時傳 alt="" 使其為裝飾性圖片。 */
export function BrandLogo({ size, className, alt, theme = 'light' }: {
  size: number
  className?: string
  alt?: string
  theme?: 'light' | 'dark'
}) {
  const brand = useBranding()
  const label = alt ?? brand.productName
  const src = theme === 'dark' ? brand.logoDarkUrl : brand.logoLightUrl
  if (!src) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 64 64"
        className={className}
        role={label ? 'img' : undefined}
        aria-label={label || undefined}
        aria-hidden={label ? undefined : true}
      >
        <rect width="64" height="64" rx="14" fill="currentColor" opacity="0.9" />
        <path d="M20 18h17l7 7v21H20z" fill="none" stroke="var(--tf-surface, #fff)" strokeWidth="4" strokeLinejoin="round" />
        <path d="M26 32h12M26 39h8" stroke="var(--tf-surface, #fff)" strokeWidth="4" strokeLinecap="round" />
      </svg>
    )
  }
  return (
    <img
      src={src}
      width={size}
      height={size}
      alt={label}
      className={className}
      draggable={false}
    />
  )
}
