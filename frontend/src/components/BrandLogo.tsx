import logoUrl from '../assets/logo.png'

/** 品牌標記（廷豐智能研報），沿用 vanilla 的 logo.png 圖片版本，取代原「廷」文字字形。
 *  alt 預設為品牌名；置於已有無障礙標籤的按鈕內時傳 alt="" 使其為裝飾性圖片。 */
export function BrandLogo({ size, className, alt = '廷豐智能研報' }: { size: number; className?: string; alt?: string }) {
  return (
    <img
      src={logoUrl}
      width={size}
      height={size}
      alt={alt}
      className={className}
      draggable={false}
    />
  )
}
