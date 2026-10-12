import { Link } from 'react-router'
import type { OpsDependencyGraph, OpsDependencyNode } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { NODE_H, NODE_W, layoutGraph } from './dependencyLayout'
import { OpsQueryError } from './OpsShared'
import { TIER_LABELS } from './opsLabels'
import { useOpsDependencies } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

type Health = OpsDependencyNode['health']

const HEALTH_LABELS: Record<Health, string> = {
  ok: '正常',
  degraded: '降級',
  down: '故障',
  unknown: '不明',
}

const KIND_LABELS: Record<OpsDependencyNode['kind'], string> = {
  systemd: 'systemd',
  container: '容器',
  external: '外部',
}

const HEALTH_PILL: Record<Health, string> = {
  ok: styles.sRunning,
  degraded: styles.sTransitioning,
  down: styles.sFailed,
  unknown: styles.sIdle,
}

const HEALTH_NODE: Record<Health, string> = {
  ok: styles.depOk,
  degraded: styles.depDegraded,
  down: styles.depDown,
  unknown: styles.depUnknown,
}

/** 外部依賴沒有探針時，「不明」其實是「未監控」——標籤講清楚，不讓人以為壞了。 */
function healthLabel(n: OpsDependencyNode): string {
  if (n.health === 'unknown' && n.kind === 'external' && !n.probe) return '未監控'
  return HEALTH_LABELS[n.health]
}

function HealthPill({ node }: { node: OpsDependencyNode }) {
  return <span className={`${styles.pill} ${HEALTH_PILL[node.health]}`}>{healthLabel(node)}</span>
}

function NodeName({ node }: { node: OpsDependencyNode }) {
  if (node.kind === 'external') return <b>{node.name}</b>
  return <Link to={`../services/${encodeURIComponent(node.name)}`} relative="path"><b>{node.name}</b></Link>
}

function Names({ names }: { names: string[] }) {
  return <>{names.join('、')}</>
}

/** 一眼看出：根因（自己壞、上游沒壞）與受影響的下游。全部正常時一行帶過。 */
function ImpactBanner({ data, byName }: { data: OpsDependencyGraph; byName: Map<string, OpsDependencyNode> }) {
  const unknown = data.nodes.filter(n => n.health === 'unknown').length
  if (data.down.length === 0) {
    return (
      <p className={styles.depAllOk} role="status">
        沒有故障的節點{unknown > 0 && `（${unknown} 個狀態不明或未監控，不列入傳播）`}
      </p>
    )
  }
  const roots = data.root_causes.length ? data.root_causes : data.down
  return (
    <div className={styles.depAlert} role="alert">
      <p className={styles.depAlertTitle}>
        {roots.length === 1 ? '根因' : '根因候選'}：
        {roots.map((name, i) => {
          const n = byName.get(name)
          return (
            <span key={name}>
              {i > 0 && '、'}<b>{name}</b>{n?.description ? `（${n.description}）` : ''}
            </span>
          )
        })}
      </p>
      {data.affected.length > 0 ? (
        <p className={styles.depAlertText}>受影響的下游（{data.affected.length}）：<Names names={data.affected} /></p>
      ) : (
        <p className={styles.depAlertText}>沒有其他節點依賴它。</p>
      )}
      {data.down.length > roots.length && (
        <p className={styles.depAlertText}>
          同時故障、但上游也壞了：<Names names={data.down.filter(n => !roots.includes(n))} />
        </p>
      )}
    </div>
  )
}

function Legend() {
  return (
    <ul className={styles.depLegend} aria-label="圖例">
      <li><span className={`${styles.depSwatch} ${styles.depOk}`} />正常</li>
      <li><span className={`${styles.depSwatch} ${styles.depDegraded}`} />降級</li>
      <li><span className={`${styles.depSwatch} ${styles.depDown}`} />故障</li>
      <li><span className={`${styles.depSwatch} ${styles.depAffected}`} />受上游影響</li>
      <li><span className={`${styles.depSwatch} ${styles.depUnknown}`} />不明／未監控</li>
      <li className={adminStyles.muted}>箭頭：上游 → 依賴它的下游</li>
    </ul>
  )
}

function Graph({ data }: { data: OpsDependencyGraph }) {
  const layout = layoutGraph(data.nodes, data.edges)
  if (layout.nodes.length === 0) {
    return <p className={adminStyles.idle}>catalog 沒有宣告任何依賴（depends_on）</p>
  }
  const label = data.down.length
    ? `依賴圖：${data.down.length} 個節點故障、${data.affected.length} 個受影響；詳細內容見下方清單`
    : `依賴圖：${layout.nodes.length} 個節點，沒有故障；詳細內容見下方清單`
  return (
    <div className={styles.depGraphWrap}>
      <svg
        className={styles.depGraph}
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        role="img"
        aria-label={label}
      >
        <defs>
          <marker id="dep-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8"
            markerUnits="userSpaceOnUse" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" className={styles.depArrow} />
          </marker>
          <marker id="dep-arrow-broken" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9"
            markerUnits="userSpaceOnUse" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" className={styles.depArrowBroken} />
          </marker>
        </defs>
        {layout.edges.map(({ edge, path }) => (
          <path
            key={`${edge.dependency}->${edge.dependent}`}
            d={path}
            className={edge.broken ? styles.depEdgeBroken : styles.depEdge}
            markerEnd={edge.broken ? 'url(#dep-arrow-broken)' : 'url(#dep-arrow)'}
            data-testid={`edge-${edge.dependency}-${edge.dependent}`}
            data-broken={edge.broken ? 'true' : 'false'}
          />
        ))}
        {layout.nodes.map(({ node, x, y }) => {
          const cls = `${styles.depNode} ${HEALTH_NODE[node.health]} ${
            node.affected && node.health !== 'down' ? styles.depAffected : ''}`
          return (
            <g key={node.name} transform={`translate(${x} ${y})`} data-testid={`node-${node.name}`}
              data-health={node.health} data-affected={node.affected ? 'true' : 'false'}>
              <title>
                {`${node.name}（${KIND_LABELS[node.kind]}・${TIER_LABELS[node.tier]}）：${healthLabel(node)}，`
                  + `${node.health_reason}${node.affected ? `；受 ${node.impacted_by.join('、')} 影響` : ''}`}
              </title>
              <rect width={NODE_W} height={NODE_H} rx={8} className={cls} />
              <text x={10} y={18} className={styles.depNodeName}>{node.name}</text>
              <text x={10} y={34} className={styles.depNodeSub}>
                {KIND_LABELS[node.kind]}・{node.affected && node.health !== 'down' ? '受影響' : healthLabel(node)}
              </text>
            </g>
          )
        })}
      </svg>
      {layout.isolated.length > 0 && (
        <p className={styles.depIsolated}>
          沒有依賴關係的節點：
          {layout.isolated.map(n => (
            <span key={n.name} className={`${styles.pill} ${HEALTH_PILL[n.health]}`} title={n.health_reason}>
              {n.name}
            </span>
          ))}
        </p>
      )}
    </div>
  )
}

/** 清單：手機上取代圖（CSS 隱藏 SVG），桌面上是圖的文字版。有問題的排前面。 */
function NodeList({ data }: { data: OpsDependencyGraph }) {
  const rank = (n: OpsDependencyNode) => (n.health === 'down' ? 0 : n.affected ? 1 : n.health === 'degraded' ? 2 : 3)
  const nodes = [...data.nodes].sort((a, b) => rank(a) - rank(b))
  return (
    <ul className={styles.depList} aria-label="依賴清單">
      {nodes.map(n => (
        <li key={n.name} className={styles.depItem} data-testid={`item-${n.name}`}>
          <div className={styles.depItemHead}>
            <NodeName node={n} />
            <HealthPill node={n} />
            {n.affected && <span className={`${styles.pill} ${styles.depAffectedPill}`}>受影響</span>}
            <span className={adminStyles.muted}>{KIND_LABELS[n.kind]}・{TIER_LABELS[n.tier]}</span>
          </div>
          {n.description && <div className={styles.depItemText}>{n.description}</div>}
          <div className={styles.depItemText}>
            {n.health_reason}
            {n.observed_at && <span className={adminStyles.muted}>（{fmtDateTime(n.observed_at)}）</span>}
          </div>
          {n.affected && (
            <div className={styles.depItemImpact}>上游故障：<Names names={n.impacted_by} /></div>
          )}
          {(n.depends_on.length > 0 || n.dependents.length > 0) && (
            <div className={adminStyles.muted}>
              {n.depends_on.length > 0 && <span>依賴 <Names names={n.depends_on} /></span>}
              {n.depends_on.length > 0 && n.dependents.length > 0 && '；'}
              {n.dependents.length > 0 && <span>被 <Names names={n.dependents} /> 依賴</span>}
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}

/**
 * 依賴圖：依賴關係只來自 Service Catalog（`depends_on`、`[[externals]]`），健康判讀與「上游壞掉 → 受影響的
 * 下游」由後端算（`GET /api/admin/ops/dependencies`）；這頁只負責畫。窄螢幕只顯示清單。
 */
export default function OpsDependenciesPage() {
  const q = useOpsDependencies()
  if (q.isPending) return <p className={adminStyles.idle}>載入中…</p>
  if (q.isError) return <OpsQueryError error={q.error} what="依賴圖" />
  const data = q.data
  const byName = new Map(data.nodes.map(n => [n.name, n]))
  return (
    <section className={adminStyles.card} aria-labelledby="ops-deps-title">
      <h2 id="ops-deps-title" className={adminStyles.ctitle}>依賴圖</h2>
      <p className={styles.meta}>
        <span>{data.environment}・{data.host}</span>
        <span>檢查時間 <b>{fmtDateTime(data.checked_at)}</b>（每 30 秒更新）</span>
      </p>
      <div className={adminStyles.spacer} />
      <ImpactBanner data={data} byName={byName} />
      <Legend />
      <Graph data={data} />
      <h3 className={styles.groupTitle}>清單</h3>
      <NodeList data={data} />
      <p className={adminStyles.hint}>
        依賴關係由主機上的 Service Catalog 宣告（<code>deploy/ops/services.*.toml</code> 的 <code>depends_on</code>）。
        外部依賴的狀態看指定探針最後一次的結果；沒有探針的是「未監控」，只有「故障」會往下游傳播。
      </p>
    </section>
  )
}
