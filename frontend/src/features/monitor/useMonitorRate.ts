/* eslint-disable react-hooks/refs, react-hooks/purity */
// Deliberate synchronous ref update during render: this is the React "store information
// from previous renders" pattern (https://react.dev/learn/you-might-not-need-an-effect
// #storing-information-from-previous-renders). Refs are mutated synchronously so the
// returned rate is always current on the same render cycle that receives the new sample.
import { useRef } from 'react'
import { NULL_RATE, nextRate, type MonitorRate, type RateBase, type RateSample } from './rate'

/** 每次有新 sample 時更新；以 ref 保存 base/last，與舊頁開頁平均行為一致。 */
export function useMonitorRate(sample: RateSample | null): MonitorRate {
  const base = useRef<RateBase | null>(null)
  const last = useRef<MonitorRate>(NULL_RATE)
  const seen = useRef<RateSample | null>(null)

  if (sample && sample !== seen.current) {
    seen.current = sample
    const out = nextRate(base.current, last.current, sample, performance.now())
    base.current = out.base
    last.current = out.rate
  }
  return last.current
}
