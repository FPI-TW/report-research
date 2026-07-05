import logoUrl from '../assets/logo.png'

/** 品牌標記（廷豐智能研報），沿用 vanilla 的 logo.png 圖片版本，取代原「廷」文字字形。 */
export function BrandLogo({ size, className }: { size: number; className?: string }) {
  return (
    <img
      src={logoUrl}
      width={size}
      height={size}
      alt="廷豐智能研報"
      className={className}
      draggable={false}
    />
  )
}
