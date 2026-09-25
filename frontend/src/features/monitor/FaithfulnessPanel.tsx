import styles from './MonitorPage.module.css'
import type { EvalSource, Evaluation } from './progressSchema'

/**
 * M8 忠實度查核健康度。
 *
 * `qa_log.evaluation` 從 M8 上線起就沒有任何讀取路徑
 * （後端、腳本、前端各 0 個消費端），查核結果只寫不看。這張卡是它的第一個出口。
 *
 * 刻意**不顯示覆蓋率百分比**：問答端有取樣率、且只查含金融數字的回答，
 * 所以「有查核的比例」天生就低，做成進度條只會長期亮紅燈而失去意義（同 takeaway
 * /signal 的教訓）。這裡呈現的是三個真的會動的訊號：
 *
 *   degraded   judge 異常時 fail-open 會寫 degraded=true、分數留 None。這條衝高代表
 *              「查核還在跑但什麼都沒查到」——最像一切正常的故障樣態。
 *   待複核     分數低於門檻的筆數。
 *   最後查核   不再前進＝整條路徑停了。
 *
 * 兩種數字刻意分開：
 *   - 主數字「已查核 checked/total」計**所有 judge**：它回答「抽查路徑有沒有在跑」，
 *     換 judge 之後不能驟降、看起來像覆蓋率崩了。
 *   - 分數類（fail-open、待複核、平均）只計**現行 judge**（判定尺）量的列：兩把尺的分數
 *     混著平均就沒有意義。所以卡片要說清楚是哪把尺、從哪天起、該尺查了幾筆、平均的樣本數
 *     多大（不含 fail-open）——切換後頭幾天樣本近乎為零，一個 0.5 的平均可能只有一筆。
 *     舊尺的筆數另外列出，不默默消失。
 */
function fmtScore(v: number | null): string {
  return v === null ? '—' : v.toFixed(3)
}

function JudgeScale({ d }: { d: EvalSource }) {
  if (!d.judge_model) return null
  const since = d.judge_since ? `，自 ${d.judge_since} 起` : ''
  const other = d.other_judge_checked ?? 0
  const counts = [
    d.judge_checked !== undefined ? `該尺已查核 ${d.judge_checked}` : null,
    d.avg_n !== undefined ? `平均樣本數 ${d.avg_n}` : null,
  ].filter(Boolean).join('、')
  return (
    <div className={styles.prate}>
      {`fail-open、待複核、平均只計判定尺 ${d.judge_model}${since}`}
      {counts && `（${counts}）`}
      {other > 0 && `；另有 ${other} 筆其他判定尺的結果只計入已查核數`}
    </div>
  )
}

function SourceRow({ label, d, min }: { label: string; d: EvalSource | null; min: number }) {
  if (!d) return null
  return (
    <>
      <div className={styles.fRow}>
        <span className={styles.fLabel}>{label}</span>
        <span className={styles.fStats}>
          <span className={styles.fStat} title="所有判定尺合計（覆蓋率）">已查核 {d.checked}/{d.total}</span>
          <span className={d.degraded > 0 ? styles.fWarn : styles.fStat}>
            fail-open {d.degraded}
          </span>
          <span className={d.below_min > 0 ? styles.fWarn : styles.fStat}>
            待複核 {d.below_min}
          </span>
          <span className={styles.fStat}>平均 {fmtScore(d.avg_score)}</span>
        </span>
        <span className={styles.fMuted}>
          {d.latest ? `最後查核 ${d.latest}` : `尚無查核（門檻 ${min}）`}
        </span>
      </div>
      <JudgeScale d={d} />
    </>
  )
}

export function FaithfulnessPanel({ evaluation }: { evaluation: Evaluation | undefined }) {
  return (
    <div className={`${styles.card} ${styles.panel}`}>
      <div className={styles.ptitle}>忠實度查核（近 30 天）</div>
      {evaluation ? (
        <>
          <SourceRow label="問答" d={evaluation.qa} min={evaluation.min_score} />
          <div className={styles.prate}>
            低於 {evaluation.min_score} 者列為待複核；fail-open 代表 judge 異常、該筆實際未被查核
          </div>
        </>
      ) : (
        <div className={styles.pidle}>此版後端未提供查核統計</div>
      )}
    </div>
  )
}
