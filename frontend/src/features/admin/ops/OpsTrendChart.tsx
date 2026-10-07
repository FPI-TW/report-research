import type { TrendPoint } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import styles from './OpsTrendChart.module.css'

const W = 600
const H = 160

type Seg = { x: number; y: number; lo: number | null; hi: number | null }[]

/** 依時間排好的點 → 連續的非空段（null 的地方斷開，不硬連）。 */
function segments(points: TrendPoint[], t0: number, t1: number, y0: number, y1: number): Seg[] {
  const span = t1 - t0 || 1
  const yspan = y1 - y0 || 1
  const sx = (t: number) => ((t - t0) / span) * W
  const sy = (v: number) => H - ((v - y0) / yspan) * H
  const out: Seg[] = []
  let cur: Seg = []
  for (const p of points) {
    if (p.value == null) {
      if (cur.length) out.push(cur)
      cur = []
      continue
    }
    const t = new Date(p.t).getTime()
    cur.push({
      x: sx(t), y: sy(p.value), lo: p.min == null ? null : sy(p.min), hi: p.max == null ? null : sy(p.max),
    })
  }
  if (cur.length) out.push(cur)
  return out
}

/**
 * 簡單的 SVG 折線圖（不引入圖表庫）：線寬不隨縮放變形、文字放在 SVG 外面（手機寬度也不會被壓扁）。
 * 每日資料帶 min／max 時畫淡色帶。null 的點斷開不連。
 */
export function OpsTrendChart({ points, label, format }: {
  points: TrendPoint[]
  label: string
  format: (v: number) => string
}) {
  const values = points.flatMap(p => [p.value, p.min, p.max]).filter((v): v is number => v != null)
  if (values.length === 0) {
    return <p className={styles.empty}>這段期間沒有資料。</p>
  }
  const times = points.map(p => new Date(p.t).getTime())
  const t0 = Math.min(...times)
  const t1 = Math.max(...times)
  let y0 = Math.min(...values)
  let y1 = Math.max(...values)
  if (y0 === y1) { y0 = y0 === 0 ? 0 : y0 * 0.9; y1 = y1 === 0 ? 1 : y1 * 1.1 }
  if (y0 > 0 && y0 < (y1 - y0)) y0 = 0  // 接近 0 時從 0 起，避免把小波動放大成懸崖
  const segs = segments(points, t0, t1, y0, y1)
  const latest = [...points].reverse().find(p => p.value != null)
  return (
    <figure className={styles.figure}>
      <div className={styles.plot}>
        <div className={styles.yaxis} aria-hidden="true">
          <span>{format(y1)}</span>
          <span>{format(y0)}</span>
        </div>
        <svg className={styles.svg} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
          aria-label={`${label}趨勢，共 ${points.length} 點${latest?.value != null ? `，最新 ${format(latest.value)}` : ''}`}>
          <line className={styles.grid} x1="0" y1="0.5" x2={W} y2="0.5" vectorEffect="non-scaling-stroke" />
          <line className={styles.grid} x1="0" y1={H - 0.5} x2={W} y2={H - 0.5} vectorEffect="non-scaling-stroke" />
          {segs.map((s, i) => {
            const band = s.every(p => p.lo != null && p.hi != null) && s.length > 1
            return (
              <g key={i}>
                {band && (
                  <polygon className={styles.band}
                    points={[...s.map(p => `${p.x},${p.hi}`), ...[...s].reverse().map(p => `${p.x},${p.lo}`)].join(' ')} />
                )}
                {s.length > 1
                  ? <polyline className={styles.line} points={s.map(p => `${p.x},${p.y}`).join(' ')}
                    vectorEffect="non-scaling-stroke" />
                  : <line className={styles.dot} x1={s[0].x} y1={s[0].y} x2={s[0].x + 0.01} y2={s[0].y}
                    vectorEffect="non-scaling-stroke" />}
              </g>
            )
          })}
        </svg>
      </div>
      <figcaption className={styles.xaxis}>
        <span>{fmtDateTime(new Date(t0).toISOString())}</span>
        {latest?.value != null && <span className={styles.latest}>最新 {format(latest.value)}</span>}
        <span>{fmtDateTime(new Date(t1).toISOString())}</span>
      </figcaption>
    </figure>
  )
}
