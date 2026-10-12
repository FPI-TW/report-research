import { describe, expect, it } from 'vitest'
import type { OpsDependencyEdge, OpsDependencyNode } from '../../../lib/generated/adminApi'
import { GAP_X, NODE_H, NODE_W, PAD, layoutGraph } from './dependencyLayout'

const node = (name: string, layer: number, depends_on: string[] = [], dependents: string[] = []): OpsDependencyNode => ({
  name, kind: 'systemd', tier: 'critical', target: null, description: '', summary: 'running', health: 'ok',
  health_reason: '執行中', probe: null, observed_at: null, depends_on, dependents, layer, affected: false,
  impacted_by: [],
})
const edge = (dependent: string, dependency: string): OpsDependencyEdge => ({ dependent, dependency, broken: false })

describe('layoutGraph', () => {
  it('依層級分欄（上游在左），孤點另外列，層級不連續時壓成連續的欄', () => {
    const nodes = [
      node('pg', 0, [], ['web']), node('probe', 0), node('web', 1, ['pg'], ['edge']), node('edge', 3, ['web']),
    ]
    const l = layoutGraph(nodes, [edge('web', 'pg'), edge('edge', 'web')])
    expect(l.isolated.map(n => n.name)).toEqual(['probe'])
    const x = Object.fromEntries(l.nodes.map(p => [p.node.name, p.x]))
    expect(x).toEqual({ pg: PAD, web: PAD + NODE_W + GAP_X, edge: PAD + 2 * (NODE_W + GAP_X) })
    expect(l.width).toBe(PAD * 2 + 3 * NODE_W + 2 * GAP_X)
    expect(l.height).toBe(PAD * 2 + NODE_H)
    expect(l.edges).toHaveLength(2)
    // 從上游的右緣畫到下游的左緣
    expect(l.edges[0].path.startsWith(`M ${PAD + NODE_W} `)).toBe(true)
  })

  it('重心法：下游依它依賴的上游排序，減少交叉', () => {
    const nodes = [
      node('a', 0, [], ['y']), node('b', 0, [], ['x']),
      node('x', 1, ['b']), node('y', 1, ['a']),
    ]
    const l = layoutGraph(nodes, [edge('x', 'b'), edge('y', 'a')])
    const y = Object.fromEntries(l.nodes.map(p => [p.node.name, p.y]))
    expect(y.a < y.b).toBe(y.y < y.x)
  })

  it('沒有邊：全部都是孤點，圖是空的', () => {
    const l = layoutGraph([node('a', 0), node('b', 0)], [])
    expect(l.nodes).toEqual([])
    expect(l.isolated).toHaveLength(2)
    expect([l.width, l.height]).toEqual([0, 0])
  })

  it('邊指向不在圖裡的節點時略過（不拋例外）', () => {
    const l = layoutGraph([node('web', 1, ['pg'])], [edge('web', 'pg')])
    expect(l.edges).toEqual([])
    expect(l.nodes.map(p => p.node.name)).toEqual(['web'])
  })
})
