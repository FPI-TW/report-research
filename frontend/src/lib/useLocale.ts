// 語言偏好（zh-Hant / en）：介面語系與問答輸出語言共用同一個偏好（`tf.locale`）。store 本體在 `src/i18n/`，
// 這裡保留 M10b 的匯入路徑（問答、設定彈窗與既有測試都從這裡取）。
export { setLocale, useLocale, type Locale } from '../i18n'
