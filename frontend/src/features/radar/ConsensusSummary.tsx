import { useCallback, useId, useMemo, useRef, useState } from 'react'
import { motion } from 'motion/react'
import { Icon } from '../../components/primitives/Icon'
import { Popover } from '../../components/primitives/Popover'
import { Pressable } from '../../components/primitives/Pressable'
import { springThumb } from '../../lib/motionTokens'
import type { BrokerSummary, RatingConsensus, Window } from '../../lib/radarSchemas'
import { copyText } from '../../lib/clipboard'
import { BrokerDotPlot } from './BrokerDotPlot'
import { brokerTableTsv } from './consensusExport'
import { ConsensusSnapshot } from './ConsensusSnapshot'
import {
  brokerKey, brokerName, buildEpsSeries, buildTargetSeries, epsBases, epsBasisKey,
  epsExclusionNote, FRESHNESS_LABEL, inScope, pickTargetCurrency, reportFreshness,
  type EpsBasis, type FreshnessScope,
} from './consensusSeries'
import { currencyPrefix, fmtDateOrNA, fmtEps, fmtPriceOrNA, NOT_PROVIDED } from './radarFormat'
import styles from './ConsensusSummary.module.css'

const SCOPE_OPTIONS: { value: FreshnessScope, label: string }[] = [
  { value: 'recent', label: `${FRESHNESS_LABEL.recent}` },
  { value: 'all', label: '含過期資料' },
]

const SCOPE_HINT
  = '這兩張圖列出所有已擷取觀點的券商，與頁面上方的時間窗無關——時間窗管的是評等共識'
  + '與近期變化。「90 天內」只保留報告日期可確認在 90 天內的券商（日期不明者一併排除，'
  + '因為無法佐證它落在期間內）；「含過期資料」顯示全部，過期者以空心點、日期不明者以'
  + '虛線點標示。'

/**
 * 「沒有券商提供目標價」與「一家券商都沒有」在畫面上長得一樣，但成因相反：
 * 前者是研報沒揭露這個數字（要去查原文），後者是資料範圍把人濾光了（要放寬範圍）。
 *
 * 「放寬範圍」的建議**只在還有得放寬時才給**——scope 已經是「含過期資料」還叫人去點
 * 「含過期資料」，是把使用者送進一個什麼都不會改變的動作。
 *
 * 刻意**沒有**第三種「有值但都不可比」的說法——那個狀態不可能發生：口徑本身就是從
 * 現有券商推導的（目標價取幣別眾數、EPS 取涵蓋最多的口徑），所以只要有任何一個值，
 * 選出來的口徑必定至少命中它自己。寫一句永遠不會出現的文案只會誤導後續維護者。
 */
function emptyReason(visibleCount: number, scope: FreshnessScope, noValue: string): string {
  if (visibleCount > 0) return noValue
  return scope === 'recent'
    ? `此標的沒有 ${FRESHNESS_LABEL.recent}的券商觀點，改選「含過期資料」可看到較舊的報告。`
    : '此標的尚無任何已擷取的券商觀點。'
}

interface Props {
  rating?: RatingConsensus | null
  /** 後端共識算出的主要幣別；缺值時改由券商之間的眾數決定。 */
  targetCurrencyHint?: string | null
  brokers: BrokerSummary[]
  window: Window
  /** 「查看券商觀點」：把該券商在「各券商最新觀點」展開並捲過去。 */
  onViewBroker: (brokerKey: string) => void
}

/**
 * 市場共識摘要：評等共識窄列 ＋ 兩張互動式水平點圖 ＋ 已選券商詳情。
 *
 * **這裡刻意沒有中位數、平均與區間。** 那些聚合值把十四家券商壓成一個數字，讀者看不出
 * 「多數集中在 280、只有一家喊 478」與「大家平均散在 200–500」的差別，而那個差別正是
 * 跨券商比較唯一有價值的東西。後端仍然照算（`target_price.groups[*].median` 等），
 * 只是這一區不再消費它——要恢復只需把值讀回來，不必動 API。
 *
 * 兩張圖與詳情面板共用同一個 `selected` 券商 key，所以「在目標價圖上點凱基」與
 * 「在 EPS 圖上點凱基」是同一個選取，不會出現兩邊各選一家的狀態。
 */
export function ConsensusSummary({
  rating, targetCurrencyHint, brokers, window, onViewBroker,
}: Props) {
  const uid = useId()
  const hintId = `${uid}-scope-hint`
  const rootRef = useRef<HTMLDivElement>(null)
  const fyTriggerRef = useRef<HTMLButtonElement>(null)
  const [scope, setScope] = useState<FreshnessScope>('all')
  const [basisKey, setBasisKey] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [tableView, setTableView] = useState(false)
  const [fyOpen, setFyOpen] = useState(false)

  /** 具名且落在目前資料範圍內的券商。兩張圖、表格與詳情面板全部只看這一份。 */
  const visible = useMemo(() => brokers.filter(broker => (
    Boolean(brokerKey(broker))
    && inScope(reportFreshness(broker.latest_report_date), scope)
  )), [brokers, scope])

  const bases = useMemo(() => epsBases(visible), [visible])
  // 受控值只在它仍然存在時採用：切換資料範圍可能讓原本選的口徑整個消失，
  // 那時要回到「涵蓋最多券商的口徑」而不是留在一個空集合上。
  const basis = bases.find(b => b.key === basisKey) ?? bases[0] ?? null

  const currency = useMemo(
    () => pickTargetCurrency(visible, targetCurrencyHint),
    [visible, targetCurrencyHint],
  )
  const targetSeries = useMemo(
    () => buildTargetSeries(visible, currency),
    [visible, currency],
  )
  const epsSeries = useMemo(() => buildEpsSeries(visible, basis), [visible, basis])

  // 選取同樣是「派生後才生效」：被範圍切換濾掉的券商不該還留著選取態。
  const selectedBroker = visible.find(broker => brokerKey(broker) === selected) ?? null
  const activeKey = selectedBroker ? selected : null

  function toggleSelect(key: string) {
    setSelected(prev => (prev === key ? null : key))
  }

  /**
   * 「清除選取」把自己從 DOM 移除，焦點會直接掉到 <body>——鍵盤使用者被丟回文件開頭。
   * 把焦點交還給那家券商在圖上的點：既是使用者剛才操作的對象，也是最可能的下一步。
   * 表格檢視下沒有點，退回同一列的券商鈕；再找不到就維持原樣（不硬搶焦點）。
   */
  /** Popover 有 focus trap 但關閉時不還原焦點，鍵盤使用者會掉回文件開頭。 */
  const closeFyMenu = useCallback(() => {
    setFyOpen(false)
    fyTriggerRef.current?.focus()
  }, [])

  const clearSelection = useCallback((key: string) => {
    setSelected(null)
    const root = rootRef.current
    const next = root?.querySelector<HTMLElement>(`[data-testid$="-dot-${key}"]`)
      ?? root?.querySelector<HTMLElement>(`[data-testid="table-pick-${key}"]`)
    next?.focus()
  }, [])

  const targetAxis = `目標價${currency ? `（${currencyPrefix(currency).trim() || currency}）` : ''}`
  const epsAxis = `EPS${basis?.currency ? `（${currencyPrefix(basis.currency).trim() || basis.currency}${basis.unit === 'per_share' ? '／股' : ''}）` : ''}`

  // 切換資料範圍／口徑／檢視會把整塊內容換掉，但視覺以外沒有任何提示。
  // 一句 polite 的現況摘要就夠了——不放在兩張圖的副標上，否則一次操作會被播報兩次。
  const status = tableView
    ? `表格檢視，共 ${visible.length} 家券商`
    : `${scope === 'all' ? '全部資料' : FRESHNESS_LABEL.recent}，`
      + `目標價 ${targetSeries.points.length} 家、`
      + `${basis?.label ?? 'EPS'} ${epsSeries.points.length} 家`

  return (
    <div className={styles.layout} ref={rootRef}>
      <p className={styles.srOnly} role="status">{status}</p>
      <div className={styles.main}>
        <ConsensusSnapshot
          rating={rating}
          window={window}
          trailing={(
            <>
              <span className={styles.scope} title={SCOPE_HINT}>
                {scope === 'all' ? '全部資料' : `僅 ${FRESHNESS_LABEL.recent}`}
                <Icon name="info" size={14} aria-hidden="true" />
              </span>
              <span id={hintId} className={styles.srOnly}>{SCOPE_HINT}</span>
              {/* layoutId 必須與 WindowSegmented 的 "window-thumb" 不同：同頁兩個相同的
                  layoutId 會被 Motion 當成同一個元素，thumb 會在兩組控制之間飛。 */}
              <span className={styles.seg} role="radiogroup" aria-label="資料範圍">
                {SCOPE_OPTIONS.map(opt => {
                  const active = opt.value === scope
                  return (
                    <Pressable
                      key={opt.value}
                      role="radio"
                      aria-checked={active}
                      // describedby 掛在每個 radio 上而不是 radiogroup 容器上：
                      // 容器不可聚焦，多數輔助技術不會在焦點進入選項時唸出容器的描述。
                      aria-describedby={hintId}
                      hoverScale={1}
                      className={`${styles.segBtn} ${active ? styles.segOn : ''}`}
                      // 一併關掉 FY 選單：切換範圍可能讓所有口徑消失，此時整個
                      // 選擇器連同 Popover 都不再渲染，而 fyOpen 會留在 true——
                      // 等口徑再度出現時選單就會自己彈開。
                      onClick={() => { setScope(opt.value); setFyOpen(false) }}
                    >
                      {active && (
                        <motion.span
                          layoutId="consensus-scope-thumb"
                          className={styles.thumb}
                          transition={springThumb}
                          aria-hidden="true"
                        />
                      )}
                      <span className={styles.segLabel}>{opt.label}</span>
                    </Pressable>
                  )
                })}
              </span>
            </>
          )}
        />

        {tableView ? (
          <BrokerDataViews
            brokers={visible}
            currency={currency}
            basis={basis}
            selected={activeKey}
            onSelect={toggleSelect}
          />
        ) : (
          <>
            <BrokerDotPlot
              title="各家最新目標價"
              axisLabel={targetAxis}
              series={targetSeries}
              selectedKey={activeKey}
              onSelect={toggleSelect}
              emptyText={emptyReason(visible.length, scope, '此範圍內沒有券商提供目標價。')}
              testPrefix="target"
            />
            <BrokerDotPlot
              title="各家 EPS"
              axisLabel={epsAxis}
              series={epsSeries}
              selectedKey={activeKey}
              onSelect={toggleSelect}
              emptyText={emptyReason(visible.length, scope, '此範圍內沒有券商提供 EPS 預估。')}
              testPrefix="eps"
              control={bases.length ? (
                <div className={styles.fyWrap}>
                  <Pressable
                    ref={fyTriggerRef}
                    type="button"
                    className={styles.fyTrigger}
                    aria-haspopup="menu"
                    aria-expanded={fyOpen}
                    aria-label={`EPS 比較口徑：${basis?.label ?? '—'}`}
                    hoverScale={1}
                    onClick={() => setFyOpen(o => !o)}
                  >
                    {basis?.label ?? '—'}
                    <Icon name="chevronDown" size={14} aria-hidden="true" />
                  </Pressable>
                  <Popover
                    open={fyOpen}
                    onClose={closeFyMenu}
                    className={styles.fyMenu}
                    ariaLabel="EPS 比較口徑"
                  >
                    {bases.map(option => (
                      <button
                        key={option.key}
                        type="button"
                        role="menuitemradio"
                        aria-checked={option.key === basis?.key}
                        className={styles.fyItem}
                        onClick={() => {
                          setBasisKey(option.key)
                          closeFyMenu()
                        }}
                      >
                        <span>{option.label}</span>
                        <span className={styles.fyCount}>{option.count} 家</span>
                      </button>
                    ))}
                  </Popover>
                </div>
              ) : undefined}
            />
          </>
        )}
      </div>

      <aside className={styles.side}>
        <Pressable
          type="button"
          className={styles.viewToggle}
          aria-pressed={tableView}
          hoverScale={1}
          onClick={() => setTableView(v => !v)}
        >
          <Icon name="filter" size={16} aria-hidden="true" />
          切換表格檢視
        </Pressable>

        {/* 只在表格檢視出現：複製的就是眼前這張表（同一個 visible／currency／basis），
            點圖檢視時沒有「一張表」可言。 */}
        {tableView && visible.length > 0 ? (
          <CopyTableButton brokers={visible} currency={currency} basis={basis} />
        ) : null}

        <div className={styles.detail}>
          <h3 className={styles.detailTitle}>已選券商</h3>
          {selectedBroker ? (
            <SelectedBroker
              broker={selectedBroker}
              basis={basis}
              onView={() => onViewBroker(brokerKey(selectedBroker))}
              onClear={() => clearSelection(brokerKey(selectedBroker))}
            />
          ) : (
            <p className={styles.detailHint}>
              點選圖上任一個點（或以 Tab 聚焦後按 Enter），這裡會顯示該券商的目標價、
              EPS 與報告日期。
            </p>
          )}
        </div>
      </aside>
    </div>
  )
}

/**
 * 把表格複製成 TSV（貼進 Excel／Sheets 自動分欄，數值是原始數字；見 consensusExport.ts）。
 * 不用 animate-ui 的 CopyButton：那顆只有圖示，而這裡需要說出「貼到哪裡」的文字標籤。
 * 失敗要說出來——區網 HTTP 下剪貼簿可能被拒，靜默失敗的話使用者貼出來的是上一次的內容。
 */
function CopyTableButton({ brokers, currency, basis }: Pick<TableProps, 'brokers' | 'currency' | 'basis'>) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onCopy = useCallback(() => {
    const done = (next: 'copied' | 'failed') => {
      setState(next)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => setState('idle'), 2000)
    }
    copyText(brokerTableTsv(brokers, currency, basis)).then(() => done('copied'), () => done('failed'))
  }, [brokers, currency, basis])
  return (
    <Pressable type="button" className={styles.viewToggle} hoverScale={1} onClick={onCopy}>
      <span aria-live="polite">
        {state === 'copied' ? '已複製，可貼入 Excel' : state === 'failed' ? '複製失敗，請再試一次' : '複製表格'}
      </span>
    </Pressable>
  )
}

/** 券商在目前 EPS 口徑下的值；口徑不符時**不回退成它自己的年度**——那會標錯年份。 */
function epsForBasis(broker: BrokerSummary, basisKeyValue: string | null): string | null {
  if (broker.latest_eps_value == null) return null
  if (basisKeyValue == null || epsBasisKey(broker) !== basisKeyValue) return null
  return fmtEps(broker.latest_eps_value, broker.latest_eps_currency, null, null, broker.latest_eps_unit)
}

interface SelectedProps {
  broker: BrokerSummary
  basis: EpsBasis | null
  onView: () => void
  onClear: () => void
}

function SelectedBroker({ broker, basis, onView, onClear }: SelectedProps) {
  const freshness = reportFreshness(broker.latest_report_date)
  const eps = epsForBasis(broker, basis?.key ?? null)
  // 說明句走 epsExclusionNote，與點圖／表格同一支：先前這裡自己重組 `FY${fy}E`，
  // 期間相同、只有幣別不同時會印出「僅提供 FY26E」而正上方的欄名就是「FY26E EPS」。
  const ownEpsNote = broker.latest_eps_value != null && eps == null
    ? epsExclusionNote(broker, basis)
    : null

  return (
    <div data-testid="consensus-selected">
      <p className={styles.detailName}>{brokerName(broker)}</p>

      <dl className={styles.detailFacts}>
        <div>
          <dt>目標價</dt>
          <dd>{fmtPriceOrNA(broker.latest_target_price, broker.latest_target_currency)}</dd>
        </div>
        <div>
          <dt>{basis?.label ? `${basis.label} EPS` : 'EPS'}</dt>
          <dd>
            {eps ?? NOT_PROVIDED}
            {ownEpsNote ? <span className={styles.detailNote}>{ownEpsNote}</span> : null}
          </dd>
        </div>
        <div>
          <dt>報告日期</dt>
          <dd>{fmtDateOrNA(broker.latest_report_date)}</dd>
        </div>
      </dl>

      <p className={`${styles.freshBadge} ${styles[freshness]}`}>{FRESHNESS_LABEL[freshness]}</p>

      <Pressable type="button" className={styles.detailBtn} hoverScale={1} onClick={onView}>
        <Icon name="fileText" size={16} aria-hidden="true" />
        查看券商觀點
      </Pressable>
      <Pressable type="button" className={styles.detailBtnGhost} hoverScale={1} onClick={onClear}>
        清除選取
      </Pressable>
    </div>
  )
}

interface TableProps {
  brokers: BrokerSummary[]
  currency: string | null
  basis: EpsBasis | null
  selected: string | null
  onSelect: (key: string) => void
}

/** 同一份資料的響應式呈現：CSS 決定桌面表格或行動卡片，狀態與格式化邏輯只留一份。 */
function BrokerDataViews(props: TableProps) {
  return (
    <>
      <BrokerTable {...props} />
      <BrokerCards {...props} />
    </>
  )
}

function BrokerCards({ brokers, currency, basis, selected, onSelect }: TableProps) {
  return (
    <>
      <ul className={styles.mobileCards} aria-label="各家目標價與 EPS 行動版">
        {brokers.map(broker => {
          const key = brokerKey(broker)
          const freshness = reportFreshness(broker.latest_report_date)
          const ownCurrency = broker.latest_target_currency?.trim() || null
          const eps = epsForBasis(broker, basis?.key ?? null)
          const isSelected = key === selected
          return (
            <li
              key={key}
              className={`${styles.mobileCard} ${isSelected ? styles.mobileCardOn : ''}`}
            >
              <button
                type="button"
                className={styles.mobilePick}
                aria-pressed={isSelected}
                aria-label={`選取${brokerName(broker)}`}
                onClick={() => onSelect(key)}
              >
                <span>{brokerName(broker)}</span>
                <span className={styles.mobilePickHint}>{isSelected ? '已選取' : '查看詳情'}</span>
              </button>

              <dl className={styles.mobileFacts}>
                <div>
                  <dt>目標價</dt>
                  <dd>
                    {broker.latest_target_price == null ? NOT_PROVIDED : (
                      <>
                        {fmtPriceOrNA(broker.latest_target_price, broker.latest_target_currency)}
                        {ownCurrency !== currency ? (
                          <span className={styles.mobileNote}>
                            {ownCurrency ?? '未標示幣別'}，未納入目前口徑
                          </span>
                        ) : null}
                      </>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>{basis?.label ? `${basis.label} EPS` : 'EPS'}</dt>
                  <dd>
                    {broker.latest_eps_value == null ? NOT_PROVIDED : eps ?? (
                      <>
                        {fmtEps(
                          broker.latest_eps_value,
                          broker.latest_eps_currency,
                          broker.latest_eps_fy,
                          broker.latest_eps_period,
                          broker.latest_eps_unit,
                        )}
                        <span className={styles.mobileNote}>{epsExclusionNote(broker, basis)}</span>
                      </>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>報告日期</dt>
                  <dd>{fmtDateOrNA(broker.latest_report_date)}</dd>
                </div>
                <div>
                  <dt>資料新鮮度</dt>
                  <dd>{FRESHNESS_LABEL[freshness]}</dd>
                </div>
              </dl>
            </li>
          )
        })}
      </ul>
      {brokers.length === 0 ? (
        <p className={styles.mobileEmpty}>此範圍內沒有券商資料。</p>
      ) : null}
    </>
  )
}

/**
 * 表格檢視：與點圖**同一份**資料，只換一種呈現。
 *
 * 值一律照原樣印，不因為幣別／口徑不符就消失——不可比是要說出口的事實，
 * 所以那一格會同時印出值與它自己的口徑標籤。
 */
function BrokerTable({ brokers, currency, basis, selected, onSelect }: TableProps) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table} aria-label="各家目標價與 EPS">
        <thead>
          <tr>
            <th scope="col">券商</th>
            <th scope="col" className={styles.num}>
              目標價{currency ? `（${currencyPrefix(currency).trim() || currency}）` : ''}
            </th>
            <th scope="col" className={styles.num}>EPS{basis?.label ? `（${basis.label}）` : ''}</th>
            <th scope="col">報告日期</th>
            <th scope="col">資料新鮮度</th>
          </tr>
        </thead>
        <tbody>
          {brokers.map(broker => {
            const key = brokerKey(broker)
            const freshness = reportFreshness(broker.latest_report_date)
            const own = broker.latest_target_currency?.trim() || null
            const eps = epsForBasis(broker, basis?.key ?? null)
            const isSelected = key === selected
            return (
              <tr key={key} className={isSelected ? styles.rowOn : ''}>
                <th scope="row" className={styles.rowHead}>
                  <button
                    type="button"
                    className={styles.pickBtn}
                    aria-pressed={isSelected}
                    data-testid={`table-pick-${key}`}
                    onClick={() => onSelect(key)}
                  >
                    {brokerName(broker)}
                  </button>
                </th>
                <td className={styles.num}>
                  {broker.latest_target_price == null ? (
                    <span className={styles.na}>{NOT_PROVIDED}</span>
                  ) : (
                    <>
                      {fmtPriceOrNA(broker.latest_target_price, broker.latest_target_currency)}
                      {own !== currency ? (
                        <span className={styles.cellNote}>
                          {own ?? '未標示幣別'}，未納入目前口徑
                        </span>
                      ) : null}
                    </>
                  )}
                </td>
                <td className={styles.num}>
                  {broker.latest_eps_value == null ? (
                    <span className={styles.na}>{NOT_PROVIDED}</span>
                  ) : eps ? eps : (
                    <>
                      {fmtEps(
                        broker.latest_eps_value,
                        broker.latest_eps_currency,
                        broker.latest_eps_fy,
                        broker.latest_eps_period,
                        broker.latest_eps_unit,
                      )}
                      {/* 說明句與點圖共用 epsExclusionNote：先前這裡寫死「未納入目前期間」，
                          於是只有幣別不同（期間相同）的券商，在圖上被說成口徑不同、
                          在表格裡被說成期間不同，而它的期間明明一樣。 */}
                      <span className={styles.cellNote}>{epsExclusionNote(broker, basis)}</span>
                    </>
                  )}
                </td>
                <td className={styles.dateCell}>{fmtDateOrNA(broker.latest_report_date)}</td>
                <td>{FRESHNESS_LABEL[freshness]}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {brokers.length === 0 ? (
        <p className={styles.tableEmpty}>此範圍內沒有券商資料。</p>
      ) : null}
    </div>
  )
}
