import { Link } from 'react-router'
import { isNewDeepSeekScale, newScaleText } from '../../../../lib/judgeScale'
import type { EvalSource, Evaluation, Extraction } from '../../../../lib/progressSchema'
import { Bar, Count, Row, TonePill } from './parts'
import { fmtInt } from './rate'
import adminStyles from '../../Admin.module.css'
import styles from './Pipeline.module.css'

function fmtScore(v: number | null): string {
  return v === null ? '—' : v.toFixed(3)
}

/**
 * 判定尺那一行。主數字「已查核」計**所有 judge**（回答「抽查路徑有沒有在跑」，換 judge 不能驟降），
 * 分數類（fail-open、低於門檻、平均）只計**現行 judge**：兩把尺的分數混著平均沒有意義。所以要說清楚
 * 是哪把尺、該尺查了幾筆、平均的樣本數多大——切換後頭幾天一個 0.5 的平均可能只有一筆。
 * 舊後端沒有量尺欄位時整行不印，不硬湊數字。
 */
function JudgeScale({ d }: { d: EvalSource }) {
  if (!d.judge_model) return null
  const other = d.other_judge_checked ?? 0
  const since = isNewDeepSeekScale(d) ? '' : d.judge_since ? `，自 ${d.judge_since} 起` : ''
  const counts = [
    d.judge_checked !== undefined ? `該尺已查核 ${fmtInt(d.judge_checked)}` : null,
    d.avg_n !== undefined ? `平均樣本數 ${fmtInt(d.avg_n)}` : null,
  ].filter(Boolean).join('、')
  return (
    <p className={adminStyles.hint}>
      {`fail-open、低於門檻與平均只計判定尺 ${d.judge_model}${since}`}
      {counts && `（${counts}）`}
      {other > 0 && `；另有 ${fmtInt(other)} 筆其他判定尺的結果只計入已查核數`}
      。fail-open 代表 judge 異常、該筆實際未被查核。
    </p>
  )
}

/**
 * M8 忠實度查核。刻意**不畫覆蓋率進度條**：問答端有取樣率、只查含數字的回答，「有查核的比例」
 * 天生就低，做成進度條只會長期亮紅燈。要看的是 fail-open、低於門檻與最後查核有沒有前進。
 */
export function FaithfulnessCard({ evaluation }: { evaluation: Evaluation | undefined }) {
  const qa = evaluation?.qa
  return (
    <section className={adminStyles.card} aria-labelledby="pipeline-faith-title">
      <h2 id="pipeline-faith-title" className={adminStyles.ctitle}>
        問答忠實度 <span className={adminStyles.muted}>近 30 天</span>
      </h2>
      {!evaluation ? (
        <p className={adminStyles.idle}>此版後端未提供查核統計</p>
      ) : !qa ? (
        <p className={adminStyles.idle}>近 30 天沒有可查核的問答</p>
      ) : (
        <>
          <div className={styles.rows}>
            <Row label="已查核">
              <div className={styles.line}>
                <span className={adminStyles.num} title="所有判定尺合計（覆蓋率）">
                  <b>{fmtInt(qa.checked)}</b> / {fmtInt(qa.total)}
                </span>
                <span className={`${adminStyles.muted} ${styles.small}`}>有取樣率、只查含數字的回答</span>
              </div>
            </Row>
            <Row label="fail-open"><span className={adminStyles.num}><Count value={qa.degraded} /></span></Row>
            <Row label="低於門檻">
              <div className={styles.line}>
                <span className={adminStyles.num}><Count value={qa.below_min} /></span>
                <span className={adminStyles.muted}>門檻 {evaluation.min_score}</span>
                <Link to="/admin/reviews">前往待複核</Link>
              </div>
            </Row>
            <Row label="平均分數">
              <div className={styles.line}>
                <span className={adminStyles.num}><b>{fmtScore(qa.avg_score)}</b></span>
                {isNewDeepSeekScale(qa) && <TonePill tone="run">{newScaleText(qa)}</TonePill>}
              </div>
            </Row>
            <Row label="最後查核">
              <span className={adminStyles.num}>{qa.latest ?? `尚無查核（門檻 ${evaluation.min_score}）`}</span>
            </Row>
          </div>
          <JudgeScale d={qa} />
        </>
      )}
    </section>
  )
}

/** extraction_log 的 stopped_at 詞彙（與 `store.STOPPED_AT` 逐字對齊）。未知值原樣顯示，不吞掉。 */
const STOPPED: readonly { key: string; label: string; color: string }[] = [
  { key: 'ingested', label: '已入庫', color: 'var(--tf-success-soft)' },
  { key: 'not_research', label: '非研報', color: 'var(--tf-text-4)' },
  { key: 'skip_admin', label: '行政件', color: 'var(--tf-border)' },
  { key: 'scanned', label: '無文字', color: 'var(--tf-warn)' },
  { key: 'extract_error', label: '抽取失敗', color: 'var(--tf-error)' },
]
const UNKNOWN_STOP_COLOR = 'var(--mkt-fallback)'

function stoppedRows(stoppedAt: Record<string, number>) {
  const known = STOPPED.filter(s => s.key in stoppedAt).map(s => ({ ...s, value: stoppedAt[s.key] }))
  const unknown = Object.entries(stoppedAt)
    .filter(([k]) => !STOPPED.some(s => s.key === k))
    .map(([k, v]) => ({ key: k, label: k, color: UNKNOWN_STOP_COLOR, value: v }))
  return [...known, ...unknown]
}

/**
 * 抽取品質與回填（E1）。回填要跑十幾個晚上，這張卡是不用 SQL 就看得到進度的地方；落點分布回答
 * 「這個檔為什麼不在語料庫」。品質只標記不擋：需複核與頁級失敗的研報仍可檢索。
 * `(unknown)` 版本是尚未回填的舊列，回填跑完會歸零。
 */
export function ExtractionCard({ extraction }: { extraction: Extraction | null | undefined }) {
  const title = (
    <h2 id="pipeline-extract-title" className={adminStyles.ctitle}>
      抽取品質與回填{extraction && <> <span className={adminStyles.muted}>目標 {extraction.target_version}</span></>}
    </h2>
  )
  if (extraction === undefined || extraction === null) {
    return (
      <section className={adminStyles.card} aria-labelledby="pipeline-extract-title">
        {title}
        <p className={adminStyles.idle}>
          {extraction === undefined
            ? '此版後端未提供抽取統計'
            : 'schema 尚未套用（extraction_log 不存在），請執行 make schema'}
        </p>
      </section>
    )
  }
  const b = extraction.backfill
  const stops = stoppedRows(extraction.stopped_at)
  const stopTotal = stops.reduce((s, r) => s + r.value, 0)
  return (
    <section className={adminStyles.card} aria-labelledby="pipeline-extract-title">
      {title}
      <div className={styles.rows}>
        <Row label="回填">
          <Bar pct={b.pct} label="回填進度" gold />
          <div className={styles.line}>
            <span className={adminStyles.num}>{b.pct.toFixed(1)}%</span>
            <span className={adminStyles.num}>已達 {extraction.target_version}：{fmtInt(b.done)}/{fmtInt(b.total)}</span>
            <span className={adminStyles.num}>尚餘 {fmtInt(b.remaining)}</span>
            <span className={adminStyles.muted}>{b.latest ? `最後寫入 ${b.latest}` : '尚無抽取紀錄'}</span>
          </div>
        </Row>
        <Row label="落點">
          {stops.length === 0 ? (
            <span className={adminStyles.muted}>extraction_log 尚無列</span>
          ) : (
            <>
              <div className={styles.stack} role="img" aria-label="抽取落點分布">
                {stops.map(s => (
                  <span key={s.key} style={{ width: `${stopTotal ? (s.value / stopTotal) * 100 : 0}%`, background: s.color }} />
                ))}
              </div>
              <div className={styles.legend}>
                {stops.map(s => (
                  <span key={s.key}>
                    <span className={styles.swatch} style={{ background: s.color }} aria-hidden="true" />
                    {s.label} {s.key === 'extract_error' ? <Count value={s.value} /> : fmtInt(s.value)}
                  </span>
                ))}
              </div>
            </>
          )}
        </Row>
        <Row label="要人看">
          <div className={styles.line}>
            <span>需複核 <Count value={extraction.needs_review} /></span>
            <span>頁級失敗 <Count value={extraction.pages_failed} /></span>
            <Link to="/admin/reviews">前往待複核</Link>
          </div>
        </Row>
        <Row label="版本">
          <div className={styles.chips}>
            {extraction.versions.map(v => <TonePill key={v.version} tone="idle">{v.version} {fmtInt(v.count)}</TonePill>)}
          </div>
        </Row>
      </div>
      <p className={adminStyles.hint}>品質只標記不擋：需複核與頁級失敗的研報仍可檢索。(unknown) 是尚未回填的舊列，回填跑完會歸零。</p>
    </section>
  )
}
