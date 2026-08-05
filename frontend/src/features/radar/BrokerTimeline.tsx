import { useId, useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import { Skeleton } from '../../components/primitives/Skeleton'
import { displayTitle } from '../../lib/displayTitle'
import type {
  BrokerSnapshot, ChangeItem, DimKey, EpsGroup, Market, ReportLink, SnapshotDiff, Window,
} from '../../lib/radarSchemas'
import { DirectionTag } from './DirectionTag'
import {
  EPS_INCOMPARABLE_NOTE, epsPeriodLabel, fmtDateOrNA, fmtEpsParts, fmtPriceOrNA,
  isReportStale, NOT_PROVIDED, RATING_BUCKET, RATING_DISPLAY, STALE_REPORT_LABEL,
  TARGET_INCOMPARABLE_NOTE, WINDOW_LABEL,
} from './radarFormat'
import { useBrokerHistory } from './useRadar'
import styles from './BrokerTimeline.module.css'

interface Props {
  code: string
  market: Market
  broker: string
  brokerDisplay?: string | null
  window: Window
  expanded: boolean
  onOpenReport: (reportId: string, fileName?: string | null) => void
}

type ThesisCells = BrokerSnapshot['thesis']
type ThesisCell = ThesisCells[number]

/**
 * 四維的固定閱讀順序。
 *
 * 後端保證四格齊全（`compute._thesis_cell` 對 `THESIS_DIMENSIONS` 無條件逐一產出），
 * **但陣列順序不是 API 契約的一部分**——照收到的順序畫，等於讓版面隨後端實作漂移。
 */
const THESIS_ORDER: DimKey[] = ['outlook', 'catalyst', 'risk', 'valuation']

function text(value?: string | null): string {
  return value?.trim() ?? ''
}

/**
 * 「一句話結論」在 API 上**沒有對應欄位**——`BrokerSnapshot` 與 `SnapshotDiff` 都沒有
 * headline／summary，唯一的一句話是四維各自的 `thesis[*].summary`（LLM 依固定 schema
 * 擷取、已轉繁體）。所以這裡不生成任何文字，只是把四維中第一句拿到最前面當結論，
 * 並在畫面上標明它出自哪一維；被拿走的那一維不再進核心論點，同一句話才不會出現兩次。
 *
 * 順序即優先序（展望 → 催化劑 → 風險 → 估值）：展望是券商對後市的總看法，最接近
 * 「這份研報想說什麼」。四維都沒有 summary 時回 null，**不退而求其次拿 evidence 充數**
 * ——evidence 是研報原句，當成結論等於替券商總結他沒說過的話。
 */
function splitThesis(cells: ThesisCells): { conclusion: ThesisCell | null; points: ThesisCell[] } {
  const ordered = [...cells]
    .sort((a, b) => THESIS_ORDER.indexOf(a.dimension) - THESIS_ORDER.indexOf(b.dimension))
    .filter(cell => text(cell.summary) || text(cell.evidence))
  const conclusion = ordered.find(cell => text(cell.summary)) ?? null
  return { conclusion, points: ordered.filter(cell => cell !== conclusion) }
}

/** 同欄位可能同時有可比較與不可比較兩筆；這裡只認「畫得出方向」的那種。 */
function movement(diff: SnapshotDiff | undefined, field: ChangeItem['field']): ChangeItem | undefined {
  return diff?.changes.find(
    change => change.field === field && change.comparable
      && (change.direction === 'up' || change.direction === 'down'),
  )
}

/**
 * EPS 的修正幅度只認「與畫面上這一筆 EPS 同一個比較群組」的那一項。
 *
 * 一份研報可以同時報多個會計年度，`diff.changes` 也就可能有多筆 EPS 變動；隨手取第一筆
 * 會把 FY26 的修正掛到 FY27 的數字旁邊——兩個值都合法、沒有任何錯誤訊息。比對鍵逐字
 * 重建後端的 `label`（`scale.diff_signals`：`f"{fiscal_year} {period} EPS"`），對不上就不標。
 */
function epsMovement(
  diff: SnapshotDiff | undefined,
  eps: EpsGroup | null,
): ChangeItem | undefined {
  if (!diff || !eps) return undefined
  const key = `${eps.fiscal_year} ${eps.period} EPS`.trim()
  return diff.changes.find(
    change => change.field === 'eps' && change.comparable && change.dimension === key
      && (change.direction === 'up' || change.direction === 'down'),
  )
}

/** 其他會計年度的 EPS 修正。畫面只放得下一個群組，但 diff 是逐群組各一筆。 */
function otherEpsMovements(
  diff: SnapshotDiff | undefined,
  shown: ChangeItem | undefined,
): ChangeItem[] {
  return (diff?.changes ?? []).filter(
    change => change.field === 'eps' && change.comparable && change !== shown
      && (change.direction === 'up' || change.direction === 'down'),
  )
}

/**
 * 不可比較一律收斂成固定說法，且同一種原因只講一次（理由見 radarFormat 的兩個常數）。
 *
 * **EPS 那句必須綁定「畫面上正在顯示的那個群組」**，不能只看「diff 裡有沒有不可比較的
 * EPS 變動」：後端對 curr 的每一筆 EPS estimate 各產一筆 change，券商每滾動一次預估年度
 * 就會同時產出「同鍵可比較」與「新群組不可比較」兩種。只看有沒有不可比較的話，畫面會
 * 變成上面掛著「上修 12.5%」、下面緊接著說「暫不比較」——生產資料實測 112 筆含不可比較
 * EPS 的 diff 裡有 66 筆（59%）長這樣，其中 20 個是**零互動就看得到**的預設展開面板。
 */
function incomparableNotes(
  diff: SnapshotDiff | undefined,
  epsShown: ChangeItem | undefined,
): string[] {
  const notes: string[] = []
  for (const change of diff?.changes ?? []) {
    if (change.comparable && change.direction !== 'incomparable') continue
    // 顯示中的群組自己就比得出來 ⇒ 不可比較的是**別的**群組，與讀者眼前的數字無關
    if (change.field === 'eps' && epsShown) continue
    const note = change.field === 'eps'
      ? EPS_INCOMPARABLE_NOTE
      : change.field === 'target_price' ? TARGET_INCOMPARABLE_NOTE : ''
    if (note && !notes.includes(note)) notes.push(note)
  }
  return notes
}

function primaryEpsOf(snap: BrokerSnapshot): EpsGroup | null {
  return snap.primary_eps ?? snap.eps[0] ?? null
}

/** 「FY27E EPS」；無年度／期間時退成單獨的「EPS」，不留空標籤。 */
function epsHeading(eps: EpsGroup | null): string {
  const label = eps ? epsPeriodLabel(eps.fiscal_year, eps.period) : ''
  return label ? `${label} EPS` : 'EPS'
}

function epsAmount(eps: EpsGroup | null): string {
  if (!eps) return NOT_PROVIDED
  return fmtEpsParts(eps.median, eps.currency, eps.fiscal_year, eps.period, eps.unit).value
}

function RatingText({ snap }: { snap: BrokerSnapshot }) {
  return (
    <span className={styles[RATING_BUCKET[snap.rating]]}>
      {snap.rating_raw || RATING_DISPLAY[snap.rating]}
    </span>
  )
}

/**
 * 評等／目標價／EPS 三個摘要。
 *
 * 變動一律以「文字 ＋ 圖示」呈現（DirectionTag 自帶方向圖示與上修／下修字樣），
 * 不靠顏色單獨表意；評等變動直接用後端算好的前後值，不在前端重排文案。
 */
function FactSummary({
  snap, diff, eps, epsChange,
}: {
  snap: BrokerSnapshot
  diff?: SnapshotDiff
  eps: EpsGroup | null
  epsChange?: ChangeItem
}) {
  const rating = movement(diff, 'rating')
  const target = movement(diff, 'target_price')
  return (
    <dl className={styles.facts}>
      <div className={styles.fact}>
        <dt className={styles.factLabel}>評等</dt>
        <dd className={styles.factValue}>
          {rating ? (
            <>
              {/* 三段之間的空白是必要的：flex 容器不會把純空白算成 flex item（版面仍由
                  gap 決定），但它們留在 textContent 裡，螢幕閱讀器才不會唸成「中立→Buy」。 */}
              <span className={styles.was}>{rating.prev_value || NOT_PROVIDED}</span>
              {' '}
              <span className={styles.arrow}>→</span>
              {' '}
              <RatingText snap={snap} />
            </>
          ) : (
            <RatingText snap={snap} />
          )}
        </dd>
      </div>
      <div className={styles.fact}>
        <dt className={styles.factLabel}>目標價</dt>
        <dd className={styles.factValue}>
          {fmtPriceOrNA(snap.target_price, snap.target_currency)}
          {target ? <DirectionTag direction={target.direction} pct={target.pct_change} /> : null}
        </dd>
      </div>
      <div className={styles.fact}>
        <dt className={styles.factLabel}>{epsHeading(eps)}</dt>
        <dd className={styles.factValue}>
          {epsAmount(eps)}
          {epsChange ? <DirectionTag direction={epsChange.direction} pct={epsChange.pct_change} /> : null}
        </dd>
      </div>
    </dl>
  )
}

/** 一句話結論 ＋ 核心論點。兩者的文字全部來自 `thesis[*].summary`，見 `splitThesis`。 */
function ThesisBlock({ cells }: { cells: ThesisCells }) {
  const { conclusion, points } = splitThesis(cells)
  if (!conclusion && !points.length) return null
  return (
    <>
      {conclusion ? (
        <div className={styles.section}>
          <span className={styles.sectionLabel}>一句話結論</span>
          <p className={styles.conclusion}>
            {text(conclusion.summary)}
            {/* 出處不是裝飾：這句話是某一維的看法被提到最前面，不是整份研報的總結。 */}
            <span className={styles.source}>（{conclusion.dimension_display}）</span>
          </p>
        </div>
      ) : null}
      {points.length ? (
        <div className={styles.section}>
          <span className={styles.sectionLabel}>核心論點（{points.length}）</span>
          <ul className={styles.points}>
            {points.map(cell => (
              <li key={cell.dimension} className={styles.point}>
                <span className={styles.dim}>{cell.dimension_display}</span>
                {/* summary 缺席才退回 evidence（研報原句）——那是有話可說時的最後出路，
                    不是預設，因為原句往往是半段話、當成論點讀會失真。 */}
                <span className={styles.pointText}>{text(cell.summary) || text(cell.evidence)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  )
}

function NoteStrip({ notes }: { notes: string[] }) {
  if (!notes.length) return null
  return (
    <div className={styles.notes}>
      {notes.map(note => (
        <p key={note} className={styles.note}>
          <Icon name="info" size={14} className={styles.noteIcon} />
          {note}
        </p>
      ))}
    </div>
  )
}

/**
 * 一份研報的內容主體（三個摘要 → 其他年度修正 → 結論與論點 → 口徑提示）。
 *
 * 抽出來的理由不只是重複：`epsMovement` 的結果同時決定「EPS 那格掛不掛修正徽章」與
 * 「口徑提示要不要出現」，兩邊各算一次就會漂移成互相矛盾的畫面。
 */
function SnapshotDetail({ snap, diff }: { snap: BrokerSnapshot; diff?: SnapshotDiff }) {
  const eps = primaryEpsOf(snap)
  const epsChange = epsMovement(diff, eps)
  const others = otherEpsMovements(diff, epsChange)
  return (
    <>
      <FactSummary snap={snap} diff={diff} eps={eps} epsChange={epsChange} />
      {others.length ? (
        <div className={styles.section}>
          {/* 摘要只放得下一個群組，但一份研報同時報 FY26 與 FY27 是常態；少了這一段，
              其他年度的修正在畫面上完全沒有位置（舊版的逐筆變動清單是看得到的）。 */}
          <span className={styles.sectionLabel}>其他年度</span>
          <ul className={styles.otherEps}>
            {others.map(change => (
              <li key={`${change.dimension}-${change.direction}`}>
                <span className={styles.otherEpsLabel}>{change.label}</span>
                <DirectionTag direction={change.direction} pct={change.pct_change} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <ThesisBlock cells={snap.thesis} />
      <NoteStrip notes={incomparableNotes(diff, epsChange)} />
    </>
  )
}

function ReportLinkButton({
  link, onOpenReport, className,
}: {
  link: ReportLink
  onOpenReport: Props['onOpenReport']
  className?: string
}) {
  return (
    <Pressable
      tapScale={0.97}
      className={className ?? styles.link}
      onClick={() => onOpenReport(link.report_id, displayTitle(link))}
    >
      查看原始研報
    </Pressable>
  )
}

/**
 * 一份較舊的研報，預設收合。
 *
 * 收合時**整段不進 DOM**（不是 CSS 隱藏、也不是 `<details>`）：一家券商動輒 5–10 份
 * 歷史研報，留在 DOM 裡即使看不見也仍要排版；而且「有沒有真的收起來」用 `queryByText`
 * 就驗得到，CSS 隱藏與 `<details>` 在 jsdom 都測不出差別。
 */
function HistoryItem({
  snap, diff, onOpenReport,
}: {
  snap: BrokerSnapshot
  diff?: SnapshotDiff
  onOpenReport: Props['onOpenReport']
}) {
  const [open, setOpen] = useState(false)
  // 以 report_id 當 id 會撞：BrokerList 同時掛了桌機表格與手機卡片**兩份**這個面板
  // （另一份只是 display: none），同一份研報於是產生兩個同名 id，aria-controls 指向誰
  // 由文件順序決定——永遠是桌機那一份，即使當下可見的是手機那一份。
  const bodyId = useId()
  const eps = primaryEpsOf(snap)
  return (
    <li className={styles.histItem}>
      {/* 刻意不加 aria-label：鈕的內容本身就是日期與三個數字，覆蓋掉會讓輔助技術
          完全讀不到這份研報的評等／目標價／EPS，只剩「展開」兩個字。 */}
      <button
        type="button"
        className={styles.histToggle}
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen(value => !value)}
      >
        <span className={styles.histDate}>{fmtDateOrNA(snap.report_date)}</span>
        {!snap.in_window ? <span className={styles.badge}>窗外</span> : null}
        <span className={styles.histFacts}>
          <span><span className={styles.histKey}>評等</span> <RatingText snap={snap} /></span>
          <span>
            <span className={styles.histKey}>目標價</span>{' '}
            {fmtPriceOrNA(snap.target_price, snap.target_currency)}
          </span>
          <span>
            <span className={styles.histKey}>{epsHeading(eps)}</span> {epsAmount(eps)}
          </span>
        </span>
        <span
          className={open ? `${styles.chev} ${styles.chevOpen}` : styles.chev}
          aria-hidden="true"
        >
          <Icon name="chevronDown" size={16} strokeWidth={2} />
        </span>
      </button>
      {open ? (
        <div id={bodyId} className={styles.histBody}>
          <SnapshotDetail snap={snap} diff={diff} />
          <ReportLinkButton link={snap.report_link} onOpenReport={onOpenReport} />
        </div>
      ) : null}
    </li>
  )
}

export function BrokerTimeline({
  code, market, broker, brokerDisplay, window, expanded, onOpenReport,
}: Props) {
  const q = useBrokerHistory(code, broker, market, window, expanded)

  if (!expanded) return null

  if (q.isLoading) {
    return (
      <div className={styles.panel} aria-busy="true" data-testid="broker-timeline-skeleton">
        <div className={styles.skel}>
          <Skeleton width={180} height={18} radius={4} />
          <Skeleton height={80} radius={8} />
          <Skeleton height={60} radius={8} />
          <Skeleton height={60} radius={8} />
        </div>
      </div>
    )
  }

  if (q.isError || !q.data) {
    return (
      <div className={styles.panel}>
        <p className={styles.error}>載入券商歷程失敗。</p>
        <Pressable tapScale={0.97} className={styles.link} onClick={() => q.refetch()}>重試</Pressable>
      </div>
    )
  }

  const data = q.data
  const name = data.broker_display || brokerDisplay || broker

  if (data.coverage_state === 'pending_extraction') {
    return (
      <div className={styles.panel}>
        <h3 className={styles.title}>{name}最新觀點</h3>
        <p className={styles.pending} role="status">此券商研報尚待觀點資料整理</p>
      </div>
    )
  }

  const [latest, ...older] = data.snapshots

  // window_empty（或任何空 snapshots）在此之前沒有分支，會渲染成一塊什麼都沒有的面板。
  if (!latest) {
    return (
      <div className={styles.panel}>
        <h3 className={styles.title}>{name}最新觀點</h3>
        <p className={styles.pending} role="status">
          {WINDOW_LABEL[window]}內沒有這家券商的研報。
        </p>
      </div>
    )
  }

  const latestDiff = data.diffs[0]

  return (
    <div className={styles.panel}>
      <div className={styles.head}>
        <div className={styles.headMain}>
          <h3 className={styles.title}>{name}最新觀點</h3>
          <span className={styles.headDate}>{fmtDateOrNA(latest.report_date)}</span>
          {!latest.in_window ? <span className={styles.badge}>窗外</span> : null}
          {/* 由報告日期與「今天」現算，不吃後端的 as_of——as_of 是全體券商的最新日期，
              拿它當基準會讓一份半年前的研報在別家有新報告時看起來還很新。 */}
          {isReportStale(latest.report_date)
            ? <span className={styles.staleNote}>{STALE_REPORT_LABEL}</span>
            : null}
        </div>
        <ReportLinkButton link={latest.report_link} onOpenReport={onOpenReport} />
      </div>

      {data.coverage_state === 'partial' ? (
        <p className={styles.quality}>部分研報仍在整理，以下只顯示已擷取內容。</p>
      ) : null}

      <SnapshotDetail snap={latest} diff={latestDiff} />

      {/* 沒有可比較的前次研報時說明為什麼——否則「這次沒有變動」與「根本無從比較」
          在畫面上長得一模一樣。note 是後端唯一會給出理由的欄位。 */}
      {!latestDiff?.changes.length && latestDiff?.note ? (
        <p className={styles.diffNote}>{latestDiff.note}</p>
      ) : null}

      {older.length ? (
        <div className={styles.history}>
          <h4 className={styles.historyTitle}>
            歷史報告
            <span className={styles.historyCount}>{older.length} 份</span>
          </h4>
          <ul className={styles.histList}>
            {older.map((snap, index) => (
              <HistoryItem
                key={snap.report_id}
                snap={snap}
                // diffs 與 snapshots 一一對應、等長同序；older 少了第一筆，故位移 1。
                diff={data.diffs[index + 1]}
                onOpenReport={onOpenReport}
              />
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
