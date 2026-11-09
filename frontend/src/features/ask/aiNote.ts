import { t } from '@/i18n'

// AI 生成揭露：要向終端使用者揭露並標示 AI 生成，所以兩個變體都顯示——空狀態（中央）是第一題送出前
// 唯一看得到的輸入框。供應商寫在使用說明「AI 生成內容」，這裡只標示是 AI。
export const aiNote = () => t('ask.composer.answersAreGeneratedByAi')
export const aiNoteWeb = () => t('ask.composer.answersAreGeneratedByAi2')
