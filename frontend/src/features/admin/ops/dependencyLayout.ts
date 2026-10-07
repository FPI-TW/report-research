import type { OpsDependencyEdge, OpsDependencyNode } from '../../../lib/generated/adminApi'

/**
 * 依賴圖的分層版面（純函式，不碰 DOM）。層級（`layer`）由後端依 catalog 的依賴算好：0＝不依賴任何節點，
 * 越右邊越下游。這裡只決定同一欄內的上下順序（重心法，減少交叉）與座標，不重算依賴、不判斷健康。
 *
 * 沒有任何邊的節點（探針、取樣器……）不進圖，另外列出（`isolated`），免得一整欄孤點把圖撐高。
 */

export const NODE_W = 168
export const NODE_H = 44
export const GAP_X = 72
export const GAP_Y = 14
export const PAD = 12

export type PlacedNode = { node: OpsDependencyNode; x: number; y: number }
export type PlacedEdge = { edge: OpsDependencyEdge; path: string }
export type Layout = {
  width: number
  height: number
  nodes: PlacedNode[]
  edges: PlacedEdge[]
  isolated: OpsDependencyNode[]
}

function barycenter(names: string[], pos: Map<string, number>): number | null {
  const vals = names.map(n => pos.get(n)).filter((v): v is number => v !== undefined)
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null
}

/** 依鄰居的平均位置排序；沒有鄰居的保持原順序（穩定排序）。 */
function reorder(column: OpsDependencyNode[], neighbours: (n: OpsDependencyNode) => string[], pos: Map<string, number>) {
  const keyed = column.map((node, i) => ({ node, i, key: barycenter(neighbours(node), pos) ?? i }))
  keyed.sort((a, b) => a.key - b.key || a.i - b.i)
  return keyed.map(k => k.node)
}

function index(columns: OpsDependencyNode[][]): Map<string, number> {
  const pos = new Map<string, number>()
  for (const col of columns) col.forEach((n, i) => pos.set(n.name, i))
  return pos
}

export function layoutGraph(nodes: OpsDependencyNode[], edges: OpsDependencyEdge[]): Layout {
  const linked = new Set<string>()
  for (const e of edges) {
    linked.add(e.dependent)
    linked.add(e.dependency)
  }
  const inGraph = nodes.filter(n => linked.has(n.name))
  const isolated = nodes.filter(n => !linked.has(n.name))

  // 層級可能不連續（孤點被拿掉）：壓成連續的欄。
  const layers = [...new Set(inGraph.map(n => n.layer))].sort((a, b) => a - b)
  let columns = layers.map(l => inGraph.filter(n => n.layer === l))

  // 重心法：先由左往右（看依賴），再由右往左（看依賴它的），再由左往右收尾。
  const deps = (n: OpsDependencyNode) => n.depends_on
  const dependents = (n: OpsDependencyNode) => n.dependents
  columns = columns.map((col, i) => (i === 0 ? col : reorder(col, deps, index(columns))))
  for (let i = columns.length - 2; i >= 0; i--) columns[i] = reorder(columns[i], dependents, index(columns))
  for (let i = 1; i < columns.length; i++) columns[i] = reorder(columns[i], deps, index(columns))

  const rows = Math.max(0, ...columns.map(c => c.length))
  const step = NODE_H + GAP_Y
  const placed = new Map<string, PlacedNode>()
  columns.forEach((col, ci) => {
    const offset = ((rows - col.length) * step) / 2
    col.forEach((node, ri) => {
      placed.set(node.name, { node, x: PAD + ci * (NODE_W + GAP_X), y: PAD + offset + ri * step })
    })
  })

  const placedEdges: PlacedEdge[] = []
  for (const edge of edges) {
    const from = placed.get(edge.dependency)
    const to = placed.get(edge.dependent)
    if (!from || !to) continue
    // 上游（依賴）在左、下游在右：從上游的右緣畫到下游的左緣。
    const x1 = from.x + NODE_W
    const y1 = from.y + NODE_H / 2
    const x2 = to.x
    const y2 = to.y + NODE_H / 2
    const dx = Math.max(24, (x2 - x1) / 2)
    placedEdges.push({ edge, path: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}` })
  }

  const width = columns.length ? PAD * 2 + columns.length * NODE_W + (columns.length - 1) * GAP_X : 0
  const height = rows ? PAD * 2 + rows * NODE_H + (rows - 1) * GAP_Y : 0
  return { width, height, nodes: [...placed.values()], edges: placedEdges, isolated }
}
