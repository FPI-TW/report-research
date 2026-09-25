import type { EvalSource } from './progressSchema'

/**
 * 「新量尺」的判準，監控忠實度卡與待複核佇列共用（DeepSeek 遷移 PR-26/27）。
 *
 * 現行判定尺是 DeepSeek，而且窗期內還有其他判定尺（換尺前的 haiku）的列：這時 judge_since
 * （窗期內現行 judge 最早的一筆）就是切換後的第一筆，日期由資料得出、不寫死。窗期內已全是
 * 新尺時 judge_since 只是窗期起點，不再稱「新」。
 */
export function isNewDeepSeekScale(d: EvalSource | null | undefined): boolean {
  return Boolean(d?.judge_model?.startsWith('deepseek-') && (d.other_judge_checked ?? 0) > 0)
}

/** 「新量尺（自 X 起，DeepSeek）」；新尺尚無查核時不編日期。 */
export function newScaleText(d: EvalSource): string {
  return `新量尺（${d.judge_since ? `自 ${d.judge_since} 起，` : '尚無查核，'}DeepSeek）`
}
