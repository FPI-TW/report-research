import type { HTMLAttributes, ReactNode } from 'react'
import { Link } from 'react-router'
import { Chip, Lamp, PageHeader, Status } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { OpsDependencyGraph, OpsDependencyNode } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { NODE_H, NODE_W, layoutGraph } from './dependencyLayout'
import { OpsQueryError } from './OpsShared'
import { TIER_LABELS } from './opsLabels'
import { NODE_KIND_LABELS, healthLabel, nodeTone, serviceHref } from './services/health'
import { useOpsDependencies } from './useOps'
import styles from './services/Services.module.css'

const HELP = (
  <>
    依賴關係由主機上的 Service Catalog 宣告（<code>deploy/ops/services.*.toml</code> 的 <code>depends_on</code> 與{' '}
    <code>[[externals]]</code>），健康判讀與「上游壞掉 → 受影響的下游」由後端算。
    外部依賴的狀態看指定探針最後一次的結果；沒有探針的是「未監控」，只有「故障」會往下游傳播。
  </>
)

/** 受上游影響、但自己沒壞：圖上與清單都另外標出來（自己就壞了的節點只標故障）。 */
const isAffected = (n: OpsDependencyNode) => n.affected && n.health !== 'down'

function Names({ names }: { names: string[] }) {
  return <>{names.join('、')}</>
}

/** 外部依賴（R2、NAS…）沒有服務詳情；其餘連到服務頁的抽屜（`?service=`）。 */
function NodeLink({ node, className, children, ...rest }: {
  node: OpsDependencyNode; className: string; children: ReactNode
} & Omit<HTMLAttributes<HTMLElement>, 'className' | 'children'>) {
  if (node.kind === 'external') return <span className={className} {...rest}>{children}</span>
  return <Link className={className} to={serviceHref(node.name)} {...rest}>{children}</Link>
}

/** 一眼看出：根因（自己壞、上游沒壞）與受影響的下游。全部正常時一行帶過。 */
function ImpactBanner({ data, byName }: { data: OpsDependencyGraph; byName: Map<string, OpsDependencyNode> }) {
  const unknown = data.nodes.filter(n => n.health === 'unknown').length
  if (data.down.length === 0) {
    return (
      <div className={c.banner} data-tone="ok" role="status">
        沒有故障的節點{unknown > 0 && `（${unknown} 個狀態不明或未監控，不列入傳播）`}
      </div>
    )
  }
  const roots = data.root_causes.length ? data.root_causes : data.down
  return (
    <div className={c.banner} data-tone="bad" role="alert">
      <div className={styles.bannerBody}>
        <p>
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
          <p>受影響的下游（{data.affected.length}）：<Names names={data.affected} /></p>
        ) : (
          <p>沒有其他節點依賴它。</p>
        )}
        {data.down.length > roots.length && (
          <p>同時故障、但上游也壞了：<Names names={data.down.filter(n => !roots.includes(n))} /></p>
        )}
      </div>
    </div>
  )
}

function Legend() {
  return (
    <ul className={styles.legend} aria-label="圖例">
      <li><Lamp tone="ok" label="" />正常</li>
      <li><Lamp tone="warn" label="" />降級</li>
      <li><Lamp tone="bad" label="" />故障</li>
      <li><Lamp tone="idle" label="" />不明／未監控</li>
      <li><span className={styles.swatch} data-kind="affected" />受上游影響</li>
      <li><span className={styles.swatch} data-kind="external" />外部依賴</li>
      <li>箭頭指向被依賴的一方</li>
    </ul>
  )
}

function nodeTitle(node: OpsDependencyNode): string {
  return `${node.name}（${NODE_KIND_LABELS[node.kind]}・${TIER_LABELS[node.tier]}）：${healthLabel(node)}，`
    + `${node.health_reason}${node.affected ? `；受 ${node.impacted_by.join('、')} 影響` : ''}`
}

function Graph({ data }: { data: OpsDependencyGraph }) {
  const layout = layoutGraph(data.nodes, data.edges)
  if (layout.nodes.length === 0) {
    return <p className={c.empty}>catalog 沒有宣告任何依賴（depends_on）</p>
  }
  const label = data.down.length
    ? `依賴圖：${data.down.length} 個節點故障、${data.affected.length} 個受影響；詳細內容見下方清單`
    : `依賴圖：${layout.nodes.length} 個節點，沒有故障；詳細內容見下方清單`
  return (
    <div className={styles.graphBody}>
      <div className={styles.graphScroll}>
        <div className={styles.graph} role="group" aria-label={label} style={{ width: layout.width, height: layout.height }}>
          <svg className={styles.graphEdges} width={layout.width} height={layout.height}
            viewBox={`0 0 ${layout.width} ${layout.height}`} aria-hidden="true">
            <defs>
              <marker id="dep-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
                markerUnits="userSpaceOnUse" orient="auto">
                <path d="M 0 0 L 10 5 L 0 10 z" className={styles.arrow} />
              </marker>
              <marker id="dep-arrow-broken" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8"
                markerUnits="userSpaceOnUse" orient="auto">
                <path d="M 0 0 L 10 5 L 0 10 z" className={styles.arrowBroken} />
              </marker>
            </defs>
            {layout.edges.map(({ edge, path }) => (
              <path
                key={`${edge.dependency}->${edge.dependent}`}
                d={path}
                className={styles.edge}
                markerEnd={edge.broken ? 'url(#dep-arrow-broken)' : 'url(#dep-arrow)'}
                data-testid={`edge-${edge.dependency}-${edge.dependent}`}
                data-broken={edge.broken ? 'true' : 'false'}
              />
            ))}
          </svg>
          {layout.nodes.map(({ node, x, y }) => (
            <NodeLink key={node.name} node={node} className={styles.node} title={nodeTitle(node)}
              aria-label={`${node.name}：${isAffected(node) ? '受上游影響' : healthLabel(node)}`}
              style={{ left: x, top: y, width: NODE_W, height: NODE_H }}
              data-testid={`node-${node.name}`} data-kind={node.kind} data-health={node.health}
              data-affected={node.affected ? 'true' : 'false'}>
              <Lamp tone={nodeTone(node)} label="" />
              <span className={styles.nodeName}>{node.name}</span>
              {isAffected(node)
                ? <span className={styles.nodeTag} data-tone="warn">受影響</span>
                : node.kind === 'external' && <span className={styles.nodeTag}>外部</span>}
            </NodeLink>
          ))}
        </div>
      </div>
      <Legend />
      {layout.isolated.length > 0 && (
        <p className={styles.isolated}>
          沒有依賴關係的節點：
          {layout.isolated.map(n => (
            <NodeLink key={n.name} node={n} className={styles.depChip} title={n.health_reason}
              data-external={n.kind === 'external' ? 'true' : undefined}>
              <Lamp tone={nodeTone(n)} label={healthLabel(n)} size={5} />{n.name}
            </NodeLink>
          ))}
        </p>
      )}
    </div>
  )
}

/** 清單：手機上取代圖（CSS 隱藏圖），桌面上是圖的文字版。有問題的排前面。 */
function NodeList({ data }: { data: OpsDependencyGraph }) {
  const rank = (n: OpsDependencyNode) => (n.health === 'down' ? 0 : n.affected ? 1 : n.health === 'degraded' ? 2 : 3)
  const nodes = [...data.nodes].sort((a, b) => rank(a) - rank(b))
  return (
    <ul className={styles.list} aria-label="依賴清單">
      {nodes.map(n => (
        <li key={n.name} className={styles.item} data-testid={`item-${n.name}`}>
          <div className={styles.itemHead}>
            <NodeLink node={n} className={styles.itemName}>{n.name}</NodeLink>
            <Status tone={nodeTone(n)}>{healthLabel(n)}</Status>
            {n.affected && <Chip tone="warn">受影響</Chip>}
            <span className={styles.itemKind}>{NODE_KIND_LABELS[n.kind]}・{TIER_LABELS[n.tier]}</span>
          </div>
          {n.description && <div className={styles.itemText}>{n.description}</div>}
          <div className={styles.itemText}>
            {n.health_reason}
            {n.observed_at && <span className={c.muted}>（{fmtDateTime(n.observed_at)}）</span>}
          </div>
          {n.affected && (
            <div className={styles.itemImpact}>上游故障：<Names names={n.impacted_by} /></div>
          )}
          {(n.depends_on.length > 0 || n.dependents.length > 0) && (
            <div className={styles.itemLinks}>
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

function summaryOf(data: OpsDependencyGraph): string {
  if (data.down.length === 0) return `${data.nodes.length} 個節點，沒有故障。`
  return `${data.nodes.length} 個節點，${data.down.length} 個故障、${data.affected.length} 個受影響。`
}

/**
 * 依賴圖：依賴關係只來自 Service Catalog（`depends_on`、`[[externals]]`），健康判讀與「上游壞掉 → 受影響的
 * 下游」由後端算（`GET /api/admin/ops/dependencies`）；這頁只負責畫。依後端給的層級由左到右排，箭頭指向
 * 被依賴的一方；點節點開該服務的詳情抽屜（服務頁的 `?service=`）。窄螢幕只顯示清單。
 */
export default function OpsDependenciesPage() {
  const q = useOpsDependencies()
  const data = q.data
  return (
    <>
      <PageHeader summary={data ? summaryOf(data) : '服務之間的依賴，以及目前受影響的下游。'} help={HELP} />
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <OpsQueryError error={q.error} what="依賴圖" />
      ) : (
        <>
          <ImpactBanner data={q.data} byName={new Map(q.data.nodes.map(n => [n.name, n]))} />
          <section className={c.card} aria-labelledby="ops-deps-graph-title">
            <div className={c.cardHead}>
              <h2 id="ops-deps-graph-title" className={c.cardTitle}>依賴圖</h2>
              <span className={c.cardHint}>只畫有依賴關係的節點；點節點看服務詳情</span>
            </div>
            <Graph data={q.data} />
          </section>
          <section className={c.card} aria-labelledby="ops-deps-list-title">
            <div className={c.cardHead}>
              <h2 id="ops-deps-list-title" className={c.cardTitle}>清單</h2>
              <span className={c.cardHint}>有問題的排前面</span>
            </div>
            <NodeList data={q.data} />
          </section>
          <p className={styles.meta}>
            <span>{q.data.environment}・{q.data.host}</span>
            <span>檢查時間 <b>{fmtDateTime(q.data.checked_at)}</b>（每 30 秒更新）</span>
          </p>
        </>
      )}
    </>
  )
}
